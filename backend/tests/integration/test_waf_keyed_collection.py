"""T-036: WAF collection as one keyed search per kind per cadence tick.

The acceptance bar is equivalence: for any window and the same data, the rows a
keyed tick persists equal, column for column, what the per-bucket path wrote
before (kept here as `_fetch_per_bucket_control`, which production never
calls). Everything else pins the guards around it: the split on truncation, the
per-tick search cap, a provider failure, the single permit, the backfill bound,
and the evaluation of every bucket a tick adds.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, inspect, select

from app.collectors.waf_endpoint_collector import WafCollectionReport, WafEndpointCollector
from app.models.enums import (
    BucketCompleteness,
    SearchFamily,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafEndpointBaseline, WafEndpointMetric, WafSourceBucket
from app.providers.base import ProviderUnavailableError
from app.providers.dto import (
    WafBreakdownBatch,
    WafBreakdownBucketBatch,
    WafBreakdownGroup,
    WafObservationBatch,
    WafObservationBucketBatch,
    WafRequestGroup,
)
from app.rules.matches import window_starts
from app.services.search_cadence import SearchCadenceService
from app.services.waf_ariel_budget import WafArielBudget, WafArielPermit
from app.services.waf_pipeline import evaluation_collector, run_waf_source
from app.waf.bucketed import fold_breakdown_buckets, fold_observation_buckets
from app.waf.profile import WafFieldMapping, breakdown_plan
from tests.integration.test_waf_breakdown_collection import AUTHORED, OUTCOMES_ON
from tests.integration.test_waf_productionization import (
    STEP,
    T0,
    StubWafProvider,
    batch,
    cfg,
    count,
    source_with_profile,
    word,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SECONDS = int(STEP.total_seconds())
KINDS = ("ACTION", "RESPONSE_CODE", "SOURCE_IP")
VALUES = {
    "ACTION": ("DENY", "ALLOW"),
    "RESPONSE_CODE": ("403", "200"),
    "SOURCE_IP": ("198.51.100.1", "198.51.100.2"),
}
Group = tuple[str, str, int]
BreakdownRow = tuple[str, str, str, int]


# ----------------------------------------------------------------- the provider
class ScriptedWaf(StubWafProvider):
    """A WAF source that answers from a script, both per bucket and keyed.

    The per-bucket methods emulate Ariel's `ORDER BY SUM(eventcount) DESC LIMIT
    n` independently of the fold; the keyed methods return the *uncut* rows of
    the window through the real fold, as a keyed search would. So the two paths
    meet only in the data.
    """

    def __init__(self, volume: dict[datetime, list[Group]]) -> None:
        super().__init__()
        self.volume = volume
        self.breakdown_script: dict[str, dict[datetime, list[BreakdownRow]]] = {
            kind: {} for kind in KINDS
        }
        #: (kind, bucket_count, window_start, truncated?) in call order.
        self.calls: list[tuple[str, int, datetime, bool | None]] = []
        #: Window starts at which a keyed volume search raises.
        self.fail_keyed_volume_at: set[datetime] = set()
        self.fail_keyed_breakdown: set[str] = set()

    def derive_breakdowns(self) -> None:
        for kind in KINDS:
            first, second = VALUES[kind]
            for start, groups in self.volume.items():
                self.breakdown_script[kind][start] = [
                    row
                    for url, method, c in groups
                    for row in ((url, method, first, c // 10), (url, method, second, c - c // 10))
                ]

    def total(self, start: datetime) -> int:
        groups = self.volume.get(start, [])
        return sum(c for *_, c in groups) + 5 if groups else 0

    @staticmethod
    def _group(url: str, method: str, c: int) -> WafRequestGroup:
        return WafRequestGroup(
            raw_url=url, raw_method=method, request_count=c, record_count=c,
            distinct_client_count=3,
        )

    # ---- per bucket (today's searches)
    async def get_waf_observations(self, **kw: Any) -> WafObservationBatch:  # type: ignore[override]
        start, limit = kw["window_start"], kw["max_groups"]
        self.calls.append(("volume", 1, start, None))
        rows = sorted(self.volume.get(start, []), key=lambda g: -g[2])[:limit]
        return WafObservationBatch(
            qradar_log_source_id=kw["qradar_log_source_id"],
            window_start=start, window_end=kw["window_end"],
            groups=[self._group(*g) for g in rows],
            total_events=self.total(start), truncated=len(rows) >= limit,
            query="SCRIPTED", total_query="SCRIPTED TOTAL", ariel_searches=2,
        )

    async def get_waf_breakdown(self, **kw: Any) -> WafBreakdownBatch:  # type: ignore[override]
        start, limit, kind = kw["window_start"], kw["max_groups"], kw["breakdown"]
        self.calls.append((kind, 1, start, None))
        if kind in self.fail_keyed_breakdown:
            raise ProviderUnavailableError("scripted breakdown failure")
        rows = sorted(self.breakdown_script[kind].get(start, []), key=lambda r: -r[3])[:limit]
        return WafBreakdownBatch(
            breakdown=kind, qradar_log_source_id=kw["qradar_log_source_id"],
            window_start=start, window_end=kw["window_end"],
            groups=[
                WafBreakdownGroup(raw_url=u, raw_method=m, raw_value=v, request_count=c)
                for u, m, v, c in rows
            ],
            truncated=len(rows) >= limit, query=f"SCRIPTED {kind}", ariel_searches=1,
        )

    # ---- keyed
    async def get_waf_observations_bucketed(  # type: ignore[override]
        self, **kw: Any
    ) -> WafObservationBucketBatch:
        start, end, size = kw["window_start"], kw["window_end"], kw["bucket_seconds"]
        starts = window_starts(start, end, size)
        if start in self.fail_keyed_volume_at:
            self.calls.append(("volume", len(starts), start, None))
            raise ProviderUnavailableError("scripted keyed volume failure")
        rows: list[dict] = []
        totals: list[dict] = []
        for bucket in starts:
            k = int(bucket.timestamp()) // size
            for url, method, c in self.volume.get(bucket, []):
                rows.append({
                    "k": k, "raw_url": url, "raw_method": method, "request_count": c,
                    "record_count": c, "distinct_clients": 3,
                })
            if self.total(bucket):
                totals.append({"k": k, "total_events": self.total(bucket)})
        if len(rows) > kw["max_rows"]:
            self.calls.append(("volume", len(starts), start, True))
            return WafObservationBucketBatch(truncated=True, rows_returned=len(rows),
                                             ariel_searches=1)
        self.calls.append(("volume", len(starts), start, False))
        return WafObservationBucketBatch(
            buckets=fold_observation_buckets(
                rows, totals, qradar_log_source_id=kw["qradar_log_source_id"],
                window_start=start, window_end=end, bucket_seconds=size,
                max_groups=kw["max_groups"], query="SCRIPTED KEYED",
                total_query="SCRIPTED KEYED TOTAL",
            ),
            rows_returned=len(rows) + len(totals), query="SCRIPTED KEYED", ariel_searches=2,
        )

    async def get_waf_breakdown_bucketed(  # type: ignore[override]
        self, **kw: Any
    ) -> WafBreakdownBucketBatch:
        start, end, size, kind = (
            kw["window_start"], kw["window_end"], kw["bucket_seconds"], kw["breakdown"],
        )
        starts = window_starts(start, end, size)
        if kind in self.fail_keyed_breakdown:
            self.calls.append((kind, len(starts), start, None))
            raise ProviderUnavailableError("scripted keyed breakdown failure")
        rows = [
            {
                "k": int(bucket.timestamp()) // size, "raw_url": u, "raw_method": m,
                "raw_value": v, "request_count": c,
            }
            for bucket in starts
            for u, m, v, c in self.breakdown_script[kind].get(bucket, [])
        ]
        if len(rows) > kw["max_rows"]:
            self.calls.append((kind, len(starts), start, True))
            return WafBreakdownBucketBatch(breakdown=kind, truncated=True,
                                           rows_returned=len(rows), ariel_searches=1)
        self.calls.append((kind, len(starts), start, False))
        return WafBreakdownBucketBatch(
            breakdown=kind,
            buckets=fold_breakdown_buckets(
                rows, breakdown=kind, qradar_log_source_id=kw["qradar_log_source_id"],
                window_start=start, window_end=end, bucket_seconds=size,
                max_groups=kw["max_groups"], query=f"SCRIPTED KEYED {kind}",
            ),
            rows_returned=len(rows), query=f"SCRIPTED KEYED {kind}", ariel_searches=1,
        )

    def keyed_calls(self, kind: str) -> list[tuple[int, datetime, bool | None]]:
        return [(n, s, t) for k, n, s, t in self.calls if k == kind and n > 1]

    def single_calls(self, kind: str) -> list[datetime]:
        return [s for k, n, s, _ in self.calls if k == kind and n == 1]


def traffic(buckets: int = 25, *, flood_at: int | None = 7, empty_at: int | None = 12):
    """Distinct counts, so the cut never depends on a tie.

    Six endpoints in every bucket; bucket `flood_at` adds 14 minor ones (20 groups,
    over a cap of 10); bucket `empty_at` has no traffic at all.
    """
    volume: dict[datetime, list[Group]] = {}
    for i in range(buckets):
        start = T0 + STEP * i
        if i == empty_at:
            volume[start] = []
            continue
        groups = [(f"/{word(j)}", "GET", 200 - 17 * j + i % 5) for j in range(6)]
        if i == flood_at:
            groups += [(f"/{word(100 + j)}", "POST", 30 + j) for j in range(14)]
        volume[start] = groups
    return volume


async def pin_cadence(session, settings, seconds: int) -> None:  # type: ignore[no-untyped-def]
    await SearchCadenceService(session, settings).set_cadence(
        SearchFamily.WAF_COLLECTION, seconds, actor="waf-keyed-test", source_ip=None
    )


async def make_source(session, provider, settings, *, qradar_id=312, instance=None):  # type: ignore[no-untyped-def]
    provider.qradar_ids = frozenset({312, 313})
    profile, source = await source_with_profile(
        session, provider, settings, qradar_id=qradar_id, instance=instance, mapping=AUTHORED
    )
    instance = await session.get(QRadarInstance, source.instance_id)
    plan = breakdown_plan(profile, settings)

    def collector(at: datetime, **kw: Any) -> WafEndpointCollector:
        return WafEndpointCollector(
            session, provider, mapping=WafFieldMapping.from_profile(profile),
            profile_version=profile.profile_version, settings=settings,
            clock=lambda: at, breakdowns=plan, **kw,
        )

    return profile, source, instance, collector


def after(bucket_index: int) -> datetime:
    """A clock just after bucket `bucket_index - 1` closed: it is the latest complete one."""
    return T0 + STEP * bucket_index + timedelta(seconds=5)


# =============================================================== the equivalence bar
EXCLUDED_BUCKET = {"log_source_id", "query_provenance", "collected_at", "collection_duration_ms"}
EXCLUDED_METRIC = {"waf_endpoint_id", "query_provenance"}
# Row bookkeeping stamped with the wall clock, like `collected_at`.
EXCLUDED_ENDPOINT = {"id", "log_source_id", "created_at", "updated_at"}


def columns(row: Any, excluded: set[str]) -> dict[str, Any]:
    return {
        attr.key: getattr(row, attr.key)
        for attr in inspect(type(row)).column_attrs
        if attr.key not in excluded
    }


async def persisted(session, source) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    buckets = (await session.scalars(
        select(WafSourceBucket).where(WafSourceBucket.log_source_id == source.id)
        .order_by(WafSourceBucket.bucket_start)
        .execution_options(populate_existing=True)
    )).all()
    endpoints = (await session.scalars(
        select(WafEndpoint).where(WafEndpoint.log_source_id == source.id)
        .execution_options(populate_existing=True)
    )).all()
    by_id = {e.id: (e.virtual_host, e.normalized_endpoint, e.http_method) for e in endpoints}
    metrics = (await session.scalars(
        select(WafEndpointMetric).where(WafEndpointMetric.waf_endpoint_id.in_(by_id))
        .execution_options(populate_existing=True)
    )).all()
    return {
        "buckets": [columns(b, EXCLUDED_BUCKET) for b in buckets],
        "endpoints": {by_id[e.id]: columns(e, EXCLUDED_ENDPOINT) for e in endpoints},
        "metrics": {
            (by_id[m.waf_endpoint_id], m.bucket_start): columns(m, EXCLUDED_METRIC)
            for m in metrics
        },
    }


async def collect_per_bucket(session, collector, source, instance, starts):  # type: ignore[no-untyped-def]
    """The pre-T-036 loop over the control fetch and the unchanged persist."""
    report = WafCollectionReport(instance.id)
    wm = await collector._get_or_create_watermark(
        instance, f"waf_endpoint_metric:{source.qradar_id}"
    )
    last = wm.watermark_at
    for start in starts:
        end = start + STEP
        fetched = await collector._fetch_per_bucket_control(source, start, end, report)
        await collector._persist_interval(
            source, start, end, fetched, watermark_at=last, report=report
        )
        last = start
    wm.watermark_at = last
    await session.flush()
    return report


class TestEquivalence:
    async def test_a_keyed_tick_persists_what_the_per_bucket_path_wrote(self, db_session) -> None:
        settings = cfg(
            **OUTCOMES_ON, waf_max_groups_per_bucket=10,
            waf_max_breakdown_groups_per_bucket=13, collection_max_backfill_intervals=12,
        )
        provider = ScriptedWaf(traffic())
        provider.derive_breakdowns()
        await pin_cadence(db_session, settings, 7200)  # 24 buckets
        _, source_a, instance, collector_a = await make_source(db_session, provider, settings)
        _, source_b, _, collector_b = await make_source(
            db_session, provider, settings, qradar_id=313, instance=instance
        )

        # The first run is one bucket by the base case, identically on both.
        for source, make in ((source_a, collector_a), (source_b, collector_b)):
            await make(after(1)).collect(instance, log_source=source)
        provider.calls.clear()

        report_a = await collector_a(after(25)).collect(instance, log_source=source_a)
        starts = [T0 + STEP * i for i in range(1, 25)]
        report_b = await collect_per_bucket(
            db_session, collector_b(after(25)), source_b, instance, starts
        )

        assert report_a.intervals_collected == 24
        a, b = await persisted(db_session, source_a), await persisted(db_session, source_b)
        # The window is what the bar asks for, and the data is not vacuous.
        assert len(a["buckets"]) == len(b["buckets"]) == 25
        truncated = [r for r in a["buckets"] if r["groups_truncated"]]
        empty = [r for r in a["buckets"] if r["group_count"] == 0]
        assert [r["bucket_start"] for r in truncated] == [T0 + STEP * 7]
        assert [r["bucket_start"] for r in empty] == [T0 + STEP * 12]
        assert truncated[0]["request_count_measurement"] == WafMeasurement.LOWER_BOUND
        assert truncated[0]["completeness"] == BucketCompleteness.PARTIAL
        assert truncated[0]["observed_endpoints"] == 10 and truncated[0]["group_count"] == 10
        assert {r["action_measurement"] for r in a["buckets"]} == {
            WafTelemetryStatus.EXACT, WafTelemetryStatus.LOWER_BOUND
        }
        assert any(m["top_clients"] is not None for m in a["metrics"].values())
        assert len(a["endpoints"]) > 6  # the flood's survivors were admitted

        assert a["buckets"] == b["buckets"]
        assert a["endpoints"] == b["endpoints"]
        assert a["metrics"] == b["metrics"]
        for name in (
            "endpoints_seen", "endpoints_created", "endpoints_reactivated", "rows_written",
            "unusable_groups", "untracked_endpoints", "untracked_requests", "partial_buckets",
            "lower_bound_buckets", "admission_truncated_buckets", "breakdown_lower_bound_buckets",
            "breakdown_failed_buckets",
        ):
            assert getattr(report_a, name) == getattr(report_b, name), name

    async def test_the_equivalence_holds_when_truncation_splits_the_window(
        self, db_session
    ) -> None:
        """Rows are the same however the window was cut into searches."""
        settings = cfg(
            **OUTCOMES_ON, waf_max_groups_per_bucket=10,
            waf_max_breakdown_groups_per_bucket=13, collection_max_backfill_intervals=12,
            ariel_multi_bucket_max_rows=40, search_cadence_max_searches_per_tick=256,
        )
        provider = ScriptedWaf(traffic())
        provider.derive_breakdowns()
        await pin_cadence(db_session, settings, 7200)
        _, source_a, instance, collector_a = await make_source(db_session, provider, settings)
        _, source_b, _, collector_b = await make_source(
            db_session, provider, settings, qradar_id=313, instance=instance
        )
        for source, make in ((source_a, collector_a), (source_b, collector_b)):
            await make(after(1)).collect(instance, log_source=source)

        report_a = await collector_a(after(25)).collect(instance, log_source=source_a)
        await collect_per_bucket(
            db_session, collector_b(after(25)), source_b, instance,
            [T0 + STEP * i for i in range(1, 25)],
        )

        assert report_a.splits > 0 and report_a.intervals_collected == 24
        a, b = await persisted(db_session, source_a), await persisted(db_session, source_b)
        assert a["buckets"] == b["buckets"]
        assert a["endpoints"] == b["endpoints"]
        assert a["metrics"] == b["metrics"]


# ==================================================================== one search per kind
class TestSearchesPerTick:
    async def test_twelve_buckets_cost_one_search_per_kind(self, db_session) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        provider.calls.clear()

        report = await make(after(13)).collect(instance, log_source=source)

        assert report.intervals_collected == 12
        window = T0 + STEP
        assert provider.calls == [
            ("volume", 12, window, False), ("ACTION", 12, window, False),
            ("RESPONSE_CODE", 12, window, False), ("SOURCE_IP", 12, window, False),
        ]
        assert report.searches_sent == 4 and report.splits == 0
        # 2 Ariel statements for the volume search, 1 per breakdown; 60 before.
        assert report.ariel_searches == 5

    async def test_provenance_names_the_search_behind_each_bucket(self, db_session) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        provider = ScriptedWaf(traffic(5, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        await make(after(5)).collect(instance, log_source=source)

        bucket = await db_session.get(WafSourceBucket, (source.id, T0 + STEP * 2))
        prov = bucket.query_provenance
        assert prov["search_window_start"] == (T0 + STEP).isoformat()
        assert prov["search_window_end"] == (T0 + STEP * 5).isoformat()
        assert prov["search_bucket_count"] == 4
        assert prov["bucket_key"] == f"starttime / {SECONDS * 1000}"
        assert prov["split_depth"] == 0
        assert prov["breakdown_searches"]["ACTION"]["search_bucket_count"] == 4
        # The base-case first bucket is a single-bucket search at depth 0.
        first = await db_session.get(WafSourceBucket, (source.id, T0))
        assert first.query_provenance["search_bucket_count"] == 1

    async def test_the_report_and_metadata_carry_the_search_counts(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10)
        provider = ScriptedWaf(traffic(6, flood_at=None, empty_at=None))
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        report = await make(after(6)).collect(instance, log_source=source)

        wm = await db_session.scalar(select(CollectionWatermark).where(
            CollectionWatermark.collector == f"waf_endpoint_metric:{source.qradar_id}"))
        assert (report.searches_sent, report.splits) == (1, 0)
        assert wm.collection_metadata["last_run_searches_sent"] == 1
        assert wm.collection_metadata["last_run_splits"] == 0
        assert wm.collection_metadata["last_run_ariel_searches"] == 2

    async def test_a_single_bucket_is_exactly_todays_per_bucket_call(self, db_session) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        provider = ScriptedWaf(traffic(3, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        _, source, instance, make = await make_source(db_session, provider, settings)

        await make(after(1)).collect(instance, log_source=source)

        assert provider.calls == [
            ("volume", 1, T0, None), ("ACTION", 1, T0, None),
            ("RESPONSE_CODE", 1, T0, None), ("SOURCE_IP", 1, T0, None),
        ]

    async def test_a_truncated_single_bucket_keeps_todays_semantics(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10)
        provider = ScriptedWaf(traffic(3, flood_at=0, empty_at=None))
        _, source, instance, make = await make_source(db_session, provider, settings)

        await make(after(1)).collect(instance, log_source=source)

        bucket = await db_session.get(WafSourceBucket, (source.id, T0))
        assert bucket.groups_truncated is True and bucket.group_count == 10
        assert bucket.request_count_measurement == WafMeasurement.LOWER_BOUND

    async def test_the_statements_per_tick_are_constant_per_bucket(self, db_session) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        provider = ScriptedWaf(traffic(20, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        await pin_cadence(db_session, settings, 3600)
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        await db_session.commit()

        statements: list[str] = []
        engine = db_session.bind.sync_engine  # type: ignore[union-attr]

        def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
            statements.append(statement)

        async def tick(upto: int) -> int:
            statements.clear()
            event.listen(engine, "before_cursor_execute", record)
            try:
                report = await make(after(upto)).collect(instance, log_source=source)
                await db_session.flush()
            finally:
                event.remove(engine, "before_cursor_execute", record)
            assert report.intervals_collected > 0
            return len(statements)

        s2 = await tick(3)    # buckets 1..2
        s4 = await tick(7)    # buckets 3..6
        s8 = await tick(15)   # buckets 7..14
        # Linear in the bucket count with one slope: no query per endpoint.
        assert s8 - s4 == 2 * (s4 - s2)
        assert (s4 - s2) % 2 == 0


# ================================================================== truncation: the split
class TestSplit:
    async def test_a_truncated_kind_is_split_in_halves_that_tile_the_window(
        self, db_session
    ) -> None:
        # 12 buckets * 6 groups = 72 rows; a budget of 40 truncates the window but
        # not a half (36).
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13,
                       ariel_multi_bucket_max_rows=40, search_cadence_max_searches_per_tick=64)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        provider.calls.clear()

        report = await make(after(13)).collect(instance, log_source=source)

        assert report.intervals_collected == 12
        window = T0 + STEP
        # Volume (72 rows) truncates; each breakdown (144 rows) truncates one level deeper.
        volume = provider.keyed_calls("volume")
        assert volume == [
            (12, window, True), (6, window, False), (6, window + STEP * 6, False),
        ]
        for kind in KINDS:
            calls = provider.keyed_calls(kind)
            kept = [(n, s) for n, s, truncated in calls if truncated is False]
            assert calls[0] == (12, window, True)
            # What was kept tiles the window: no gap, no overlap, in order.
            cursor = window
            for n, s in kept:
                assert s == cursor
                cursor = s + STEP * n
            assert cursor == window + STEP * 12
        assert report.splits == 1 + 3 * 3  # volume once; each breakdown 12 and both 6s
        assert report.searches_sent == len(provider.calls)
        # A bucket's provenance names the search that produced it.
        late = await db_session.get(WafSourceBucket, (source.id, T0 + STEP * 8))
        assert late.query_provenance["search_bucket_count"] == 6
        assert late.query_provenance["split_depth"] == 1
        assert late.query_provenance["search_window_start"] == (window + STEP * 6).isoformat()

    async def test_a_truncated_search_keeps_no_bucket_of_that_kind(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10, ariel_multi_bucket_max_rows=1,
                       search_cadence_max_searches_per_tick=3)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)

        report = await make(after(13)).collect(instance, log_source=source)

        # Three searches (12, 6, 3 buckets) all truncate and the cap ends the tick:
        # a truncated search keeps no bucket, so nothing is written.
        assert report.intervals_collected == 0 and report.search_cap_reached
        buckets = (await db_session.scalars(select(WafSourceBucket.bucket_start))).all()
        assert list(buckets) == [T0]

    async def test_the_recursion_ends_at_todays_per_bucket_search(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10, ariel_multi_bucket_max_rows=1,
                       search_cadence_max_searches_per_tick=64)
        provider = ScriptedWaf(traffic(6, flood_at=None, empty_at=None))
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        provider.calls.clear()

        report = await make(after(5)).collect(instance, log_source=source)  # 4 buckets

        assert report.intervals_collected == 4
        assert [(n, t) for n, _, t in provider.keyed_calls("volume")] == [
            (4, True), (2, True), (2, True),
        ]
        assert provider.single_calls("volume") == [T0 + STEP * i for i in range(1, 5)]


class TestSearchCap:
    # A budget of 80 rows holds the twelve-bucket volume search (72 rows) but not a
    # twelve-bucket breakdown (144); a six-bucket breakdown (72) fits.
    async def _window(self, db_session, cap: int):  # type: ignore[no-untyped-def]
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13,
                       ariel_multi_bucket_max_rows=80, search_cadence_max_searches_per_tick=cap)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        provider.calls.clear()
        report = await make(after(13)).collect(instance, log_source=source)
        return provider, source, report

    async def test_the_cap_keeps_only_buckets_every_kind_completed(self, db_session) -> None:
        # Volume, then three truncated breakdowns, then the left half of each: seven.
        provider, _source, report = await self._window(db_session, 7)

        assert report.searches_sent == 7 and report.search_cap_reached
        assert report.intervals_collected == 6
        assert report.watermark_at == T0 + STEP * 6
        # The volume search covered all twelve, but the right half has no breakdown.
        assert provider.keyed_calls("volume") == [(12, T0 + STEP, False)]
        starts = (await db_session.scalars(
            select(WafSourceBucket.bucket_start).order_by(WafSourceBucket.bucket_start)
        )).all()
        assert list(starts) == [T0 + STEP * i for i in range(0, 7)]

    async def test_a_kind_missing_from_the_front_stops_the_run_whatever_follows(
        self, db_session
    ) -> None:
        # Six searches: the left half has ACTION and RESPONSE_CODE but not SOURCE_IP.
        # Volume covers every bucket, and still no bucket may be written.
        provider, _, report = await self._window(db_session, 6)

        assert report.searches_sent == 6 and report.search_cap_reached
        assert report.intervals_collected == 0
        assert report.watermark_at == T0
        assert provider.keyed_calls("volume") == [(12, T0 + STEP, False)]

    async def test_the_default_cap_is_enough_for_one_split_of_every_kind(
        self, db_session
    ) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        assert settings.search_cadence_max_searches_per_tick == 16
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        report = await make(after(13)).collect(instance, log_source=source)
        assert not report.search_cap_reached and report.intervals_collected == 12


class TestProviderFailure:
    async def test_a_failure_keeps_the_complete_prefix_and_holds_the_watermark(
        self, db_session
    ) -> None:
        settings = cfg(waf_max_groups_per_bucket=10, ariel_multi_bucket_max_rows=40,
                       search_cadence_max_searches_per_tick=64)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.fail_keyed_volume_at = {T0 + STEP * 7}  # the right half
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        wm = await db_session.scalar(select(CollectionWatermark).where(
            CollectionWatermark.collector == f"waf_endpoint_metric:{source.qradar_id}"))
        wm.consecutive_failures = 2
        wm.last_error = "earlier"
        await db_session.flush()

        report = await make(after(13)).collect(instance, log_source=source)

        assert report.provider_failure_class == "ProviderUnavailableError"
        assert report.intervals_collected == 6 and report.watermark_at == T0 + STEP * 6
        # Not split around: one failed search, no retry, no per-bucket fallback.
        assert [(n, s) for n, s, _ in provider.keyed_calls("volume")] == [
            (12, T0 + STEP), (6, T0 + STEP), (6, T0 + STEP * 7),
        ]
        assert provider.single_calls("volume") == [T0]
        # A partial run does not wipe the failure record.
        await db_session.refresh(wm)
        assert (wm.consecutive_failures, wm.last_error) == (2, "earlier")
        assert wm.collection_metadata["last_provider_failure_class"] == "ProviderUnavailableError"
        assert "last_success_at" not in wm.collection_metadata or (
            wm.collection_metadata["last_success_at"] != after(13).isoformat()
        )

    async def test_a_failure_before_any_complete_bucket_surfaces_and_writes_nothing(
        self, db_session
    ) -> None:
        settings = cfg(waf_max_groups_per_bucket=10)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.fail_keyed_volume_at = {T0 + STEP}
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        await db_session.commit()

        with pytest.raises(ProviderUnavailableError):
            await make(after(13)).collect(instance, log_source=source)

        await db_session.rollback()
        starts = (await db_session.scalars(select(WafSourceBucket.bucket_start))).all()
        assert list(starts) == [T0]

    async def test_a_failed_breakdown_is_recorded_failed_for_each_bucket_it_covered(
        self, db_session
    ) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        provider = ScriptedWaf(traffic(6, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        provider.fail_keyed_breakdown = {"SOURCE_IP"}
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)

        report = await make(after(6)).collect(instance, log_source=source)

        # The volume searches are whole, so the tick is not stopped.
        assert report.intervals_collected == 5 and report.provider_failure_class is None
        assert len(report.breakdown_failed_buckets["SOURCE_IP"]) == 5
        for i in range(1, 6):
            bucket = await db_session.get(WafSourceBucket, (source.id, T0 + STEP * i))
            assert bucket.client_measurement == WafTelemetryStatus.FAILED
            assert bucket.action_measurement == WafTelemetryStatus.EXACT
            assert bucket.request_count_measurement == WafMeasurement.EXACT
            assert bucket.query_provenance["breakdowns"]["SOURCE_IP"]["error_class"] == (
                "ProviderUnavailableError"
            )


# ==================================================================== permit and window
class CountingBudget(WafArielBudget):
    def __init__(self, settings, *, grant: bool = True) -> None:  # type: ignore[no-untyped-def]
        super().__init__(None, settings)
        self.entered = 0
        self.grant = grant

    @asynccontextmanager
    async def permit(self):  # type: ignore[no-untyped-def, override]
        self.entered += 1
        yield WafArielPermit(slot=0, waited_seconds=0.0) if self.grant else None


class TestPermitAndWindow:
    async def test_one_permit_covers_the_whole_tick(self, db_session) -> None:
        settings = cfg(**OUTCOMES_ON, waf_max_groups_per_bucket=10,
                       waf_max_breakdown_groups_per_bucket=13)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        provider.derive_breakdowns()
        budget = CountingBudget(settings)
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1), ariel_budget=budget).collect(instance, log_source=source)
        assert budget.entered == 1  # the first, single-bucket run

        report = await make(after(13), ariel_budget=budget).collect(instance, log_source=source)

        assert report.intervals_collected == 12
        assert budget.entered == 2  # one more for twelve buckets, not twelve more

    async def test_no_permit_collects_nothing_and_holds_the_watermark(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10)
        provider = ScriptedWaf(traffic(14, flood_at=None, empty_at=None))
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)
        provider.calls.clear()

        report = await make(after(13), ariel_budget=CountingBudget(settings, grant=False)).collect(
            instance, log_source=source
        )

        assert report.capacity_unavailable and report.intervals_collected == 0
        assert report.watermark_at == T0 and provider.calls == []

    async def test_a_window_with_nothing_due_asks_for_no_permit(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10)
        provider = ScriptedWaf(traffic(3, flood_at=None, empty_at=None))
        budget = CountingBudget(settings)
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1), ariel_budget=budget).collect(instance, log_source=source)

        report = await make(after(1), ariel_budget=budget).collect(instance, log_source=source)

        assert report.intervals_collected == 0 and budget.entered == 1

    async def test_at_a_five_minute_cadence_the_bound_is_todays_one_plus_backfill(
        self, db_session
    ) -> None:
        settings = cfg(waf_max_groups_per_bucket=10, collection_max_backfill_intervals=3)
        provider = ScriptedWaf(traffic(40, flood_at=None, empty_at=None))
        await pin_cadence(db_session, settings, 300)
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)

        report = await make(after(30)).collect(instance, log_source=source)

        assert report.intervals_collected == 1 + 3

    async def test_at_the_default_hour_cadence_the_window_is_twelve_plus_backfill(
        self, db_session
    ) -> None:
        settings = cfg(waf_max_groups_per_bucket=10, collection_max_backfill_intervals=3)
        provider = ScriptedWaf(traffic(40, flood_at=None, empty_at=None))
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)

        report = await make(after(30)).collect(instance, log_source=source)

        assert report.intervals_collected == 12 + 3

    async def test_a_longer_cadence_widens_the_window(self, db_session) -> None:
        settings = cfg(waf_max_groups_per_bucket=10, collection_max_backfill_intervals=2)
        provider = ScriptedWaf(traffic(60, flood_at=None, empty_at=None))
        await pin_cadence(db_session, settings, 7200)
        _, source, instance, make = await make_source(db_session, provider, settings)
        await make(after(1)).collect(instance, log_source=source)

        report = await make(after(55)).collect(instance, log_source=source)

        assert report.intervals_collected == 24 + 2


# ========================================================================== evaluation
class TestEvaluationOfATick:
    async def test_every_bucket_of_a_twelve_bucket_tick_is_evaluated_in_order(
        self, db_session, monkeypatch
    ) -> None:
        from app.waf.engine import WafAnomalyEngine

        settings = cfg()
        base = [("/portal/login", "POST", 100), ("/other", "GET", 60)]
        provider = StubWafProvider([batch(T0 + STEP * i, base) for i in range(14)])
        profile, source = await source_with_profile(db_session, provider, settings)
        profile_id = profile.id

        seen: list[datetime] = []
        original = WafAnomalyEngine.evaluate_bucket

        async def recording(self, log_source, bucket):  # type: ignore[no-untyped-def]
            seen.append(bucket.bucket_start)
            return await original(self, log_source, bucket)

        monkeypatch.setattr(WafAnomalyEngine, "evaluate_bucket", recording)

        async def run(upto: int) -> dict:
            return await run_waf_source(
                db_session, profile_id, provider_factory=lambda _i: provider,
                settings=settings, clock=lambda: after(upto),
            )

        first = await run(1)
        assert first["intervals_collected"] == 1 and seen == [T0]
        seen.clear()

        tick = await run(13)

        assert tick["status"] == "ok" and tick["intervals_collected"] == 12
        expected = [T0 + STEP * i for i in range(1, 13)]
        assert seen == expected  # all twelve, oldest first
        assert tick["evaluated_buckets"] == 12
        assert tick["evaluated_through"] == expected[-1].isoformat()
        evaluation = await db_session.scalar(select(CollectionWatermark).where(
            CollectionWatermark.collector == evaluation_collector(source)))
        assert evaluation.watermark_at == expected[-1]
        # Training sees them: the baselines were rebuilt through the last evaluated bucket
        # and count all thirteen buckets as candidates.
        assert tick["baseline_train_through"] == (expected[-1] + STEP).isoformat()
        assert tick["baseline_endpoints_rebuilt"] == 2
        cells = (await db_session.scalars(select(WafEndpointBaseline))).all()
        per_endpoint: dict[uuid.UUID, int] = {}
        for cell in cells:
            per_endpoint[cell.waf_endpoint_id] = (
                per_endpoint.get(cell.waf_endpoint_id, 0)
                + cell.sample_count + cell.excluded_sample_count
            )
        assert set(per_endpoint.values()) == {13}
        assert await count(db_session, WafSourceBucket) == 13

    async def test_the_evaluation_bound_follows_the_cadence_window(self, db_session) -> None:
        """At an 8 h cadence a tick persists 96 buckets, and one run judges all of them."""
        settings = cfg()
        assert settings.waf_max_evaluation_buckets_per_run == 24
        provider = StubWafProvider(
            [batch(T0 + STEP * i, [("/portal/login", "POST", 100)]) for i in range(100)]
        )
        await pin_cadence(db_session, settings, 28_800)
        profile, _ = await source_with_profile(db_session, provider, settings)
        profile_id = profile.id

        async def run(upto: int) -> dict:
            return await run_waf_source(
                db_session, profile_id, provider_factory=lambda _i: provider,
                settings=settings, clock=lambda: after(upto),
            )

        await run(1)
        tick = await run(97)

        assert tick["intervals_collected"] == 96
        assert tick["evaluated_buckets"] == 96
        assert tick["evaluated_through"] == (T0 + STEP * 96).isoformat()

    async def test_at_an_hour_cadence_a_cap_below_the_window_is_raised_to_it(
        self, db_session
    ) -> None:
        settings = cfg(waf_max_evaluation_buckets_per_run=1)
        provider = StubWafProvider(
            [batch(T0 + STEP * i, [("/portal/login", "POST", 100)]) for i in range(14)]
        )
        profile, _ = await source_with_profile(db_session, provider, settings)
        profile_id = profile.id

        async def run(upto: int) -> dict:
            return await run_waf_source(
                db_session, profile_id, provider_factory=lambda _i: provider,
                settings=settings, clock=lambda: after(upto),
            )

        await run(1)
        tick = await run(13)

        assert tick["intervals_collected"] == 12
        assert tick["evaluated_buckets"] == 12
