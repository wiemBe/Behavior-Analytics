"""Phase B.2: breakdown collection, end to end against PostgreSQL.

A validated profile produces a `BreakdownPlan`; the collector runs one bounded
search per planned breakdown inside each bucket and records, per breakdown, what
it established (`WafTelemetryStatus`). The assertions that matter are the
negative ones: a failed or skipped breakdown leaves NULL -- never 0 -- and never
costs the bucket its request-rate observation.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.collectors.waf_endpoint_collector import WafEndpointCollector
from app.models.enums import (
    BucketCompleteness,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.instance import QRadarInstance
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket
from app.providers.base import ProviderUnavailableError
from app.providers.dto import WafBreakdownBatch, WafBreakdownGroup
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.profile import WafFieldMapping, breakdown_plan
from app.waf.semantics import breakdown_semantics
from tests.integration.test_waf_productionization import (
    LAB_MAPPING,
    STEP,
    T0,
    StubWafProvider,
    batch,
    cfg,
    source_with_profile,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

AUTHORED = WafFieldMapping(
    url="URL",
    method="HTTP_Method",
    action="WAF_Action",
    response_code="WAF_ResponseCode",
    user_agent="WAF_USERAGENT",
    blocked_actions=("DENY",),
    allowed_actions=("ALLOW",),
)
LOGIN = ("/portal/login", "POST")
STATUS = ("/api/v1/status", "GET")
VOLUME = [(*LOGIN, 100), (*STATUS, 50)]
OUTCOMES_ON = {"waf_outcome_collection_enabled": True, "waf_client_collection_enabled": True}

#: (raw URL, method, value, request count)
Row = tuple[str, str, str, int]
ACTIONS: list[Row] = [
    (*LOGIN, "DENY", 30), (*LOGIN, "ALLOW", 60), (*LOGIN, "CHALLENGE", 10),
]
CODES: list[Row] = [
    (*LOGIN, "200", 70), (*LOGIN, "403", 20), (*LOGIN, "500", 5), (*LOGIN, "weird", 5),
]
CLIENTS: list[Row] = [
    (*LOGIN, "198.51.100.1", 50), (*LOGIN, "198.51.100.2", 30), (*LOGIN, "203.0.113.9", 20),
]


def breakdown_batch(
    kind: str, start, rows: list[Row], *, truncated: bool = False
) -> WafBreakdownBatch:
    return WafBreakdownBatch(
        breakdown=kind,
        qradar_log_source_id=312,
        window_start=start,
        window_end=start + STEP,
        groups=[
            WafBreakdownGroup(raw_url=u, raw_method=m, raw_value=v, request_count=n)
            for u, m, v, n in rows
        ],
        truncated=truncated,
        query=f"STUB {kind}",
        ariel_searches=1,
    )


class BreakdownProvider(StubWafProvider):
    """The WAF stub plus per-(breakdown, window) breakdown results or errors."""

    def __init__(self, batches=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(batches)
        self.results: dict[tuple[str, object], WafBreakdownBatch | Exception] = {}
        self.breakdown_calls: list[dict] = []

    def serve(self, kind: str, start, rows=None, *, truncated=False, error=None) -> None:  # type: ignore[no-untyped-def]
        self.results[(kind, start)] = (
            error if error is not None else breakdown_batch(kind, start, rows or [],
                                                            truncated=truncated)
        )

    async def get_waf_breakdown(self, **kw):  # type: ignore[no-untyped-def, override]
        self.breakdown_calls.append(kw)
        found = self.results.get((kw["breakdown"], kw["window_start"]))
        if isinstance(found, Exception):
            raise found
        if found is None:
            return breakdown_batch(kw["breakdown"], kw["window_start"], [])
        return found


async def setup(session, provider, settings, mapping=AUTHORED):  # type: ignore[no-untyped-def]
    profile, source = await source_with_profile(session, provider, settings, mapping=mapping)
    plan = breakdown_plan(profile, settings)
    instance = await session.get(QRadarInstance, source.instance_id)

    def collector(at) -> WafEndpointCollector:  # type: ignore[no-untyped-def]
        return WafEndpointCollector(
            session, provider, mapping=WafFieldMapping.from_profile(profile),
            profile_version=profile.profile_version, settings=settings,
            clock=lambda: at, breakdowns=plan,
        )

    return profile, source, instance, plan, collector


async def collect_bucket(session, provider, settings, mapping=AUTHORED):  # type: ignore[no-untyped-def]
    """One bucket (T0), collected under the profile's own breakdown plan."""
    profile, source, instance, plan, collector = await setup(
        session, provider, settings, mapping
    )
    report = await collector(T0 + STEP + timedelta(seconds=5)).collect(
        instance, log_source=source
    )
    bucket = await session.get(WafSourceBucket, (source.id, T0))
    return profile, plan, report, bucket


async def metric_rows(session) -> dict[str, WafEndpointMetric]:  # type: ignore[no-untyped-def]
    rows = (await session.execute(
        select(WafEndpoint.normalized_endpoint, WafEndpointMetric).join(
            WafEndpointMetric, WafEndpointMetric.waf_endpoint_id == WafEndpoint.id
        )
    )).all()
    return dict(rows)


def identity(kind: str, profile) -> str:  # type: ignore[no-untyped-def]
    return breakdown_semantics(
        kind, aggregation="SUM(eventcount)", normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        profile_version=profile.profile_version,
    )


def served(settings_extra=None, **cfg_kw):  # type: ignore[no-untyped-def]
    settings = cfg(**{**OUTCOMES_ON, **(settings_extra or {}), **cfg_kw})
    provider = BreakdownProvider([batch(T0, VOLUME)])
    provider.serve("ACTION", T0, ACTIONS)
    provider.serve("RESPONSE_CODE", T0, CODES)
    provider.serve("SOURCE_IP", T0, CLIENTS)
    return settings, provider


# ================================================================== exact path
class TestExactBreakdowns:
    async def test_a_bucket_records_exact_status_semantics_and_per_endpoint_counts(
        self, db_session
    ) -> None:
        settings, provider = served()
        profile, plan, report, bucket = await collect_bucket(db_session, provider, settings)

        assert plan.collect == {
            WafBreakdown.ACTION, WafBreakdown.RESPONSE_CODE, WafBreakdown.SOURCE_IP
        }
        assert bucket.action_measurement == WafTelemetryStatus.EXACT
        assert bucket.response_code_measurement == WafTelemetryStatus.EXACT
        assert bucket.client_measurement == WafTelemetryStatus.EXACT
        # Identities are the breakdown's own, distinct from request_count's.
        assert bucket.action_semantics == identity("ACTION", profile)
        assert bucket.response_code_semantics == identity("RESPONSE_CODE", profile)
        assert bucket.client_semantics == identity("SOURCE_IP", profile)
        assert len({bucket.action_semantics, bucket.response_code_semantics,
                    bucket.client_semantics, bucket.metric_semantics}) == 4
        assert (bucket.action_events, bucket.response_code_events, bucket.client_events) == (
            100, 100, 100
        )
        assert report.breakdown_failed_buckets == {}
        assert report.breakdown_lower_bound_buckets == {}
        # 2 volume searches are the stub's; each breakdown adds the 1 it reports.
        assert report.ariel_searches == 3

        login = (await metric_rows(db_session))["/portal/login"]
        assert login.request_count == 100
        assert (login.action_events, login.blocked_count, login.unmapped_action_count) == (
            100, 30, 10
        )
        assert (
            login.response_code_events, login.client_error_count,
            login.server_error_count, login.unclassified_response_count,
        ) == (100, 20, 5, 5)
        assert (
            login.client_events, login.top1_client_requests, login.top5_client_requests,
            login.client_sources,
        ) == (100, 50, 100, 3)
        assert login.top_clients == [
            {"source_ip": "198.51.100.1", "requests": 50},
            {"source_ip": "198.51.100.2", "requests": 30},
            {"source_ip": "203.0.113.9", "requests": 20},
        ]

    async def test_a_403_is_a_client_error_and_not_a_block(self, db_session) -> None:
        settings, provider = served()
        provider.serve("ACTION", T0, [(*LOGIN, "ALLOW", 100)])
        provider.serve("RESPONSE_CODE", T0, [(*LOGIN, "403", 100)])
        await collect_bucket(db_session, provider, settings)

        login = (await metric_rows(db_session))["/portal/login"]
        assert login.client_error_count == 100
        assert login.blocked_count == 0

    async def test_a_tracked_endpoint_absent_from_an_exact_breakdown_reads_zero(
        self, db_session
    ) -> None:
        settings, provider = served()
        await collect_bucket(db_session, provider, settings)

        status = (await metric_rows(db_session))["/api/v1/status"]
        assert status.request_count == 50
        assert (status.action_events, status.blocked_count, status.unmapped_action_count) == (
            0, 0, 0
        )
        assert (status.response_code_events, status.client_error_count) == (0, 0)
        assert (status.client_events, status.top1_client_requests, status.client_sources) == (
            0, 0, 0
        )
        # Nothing material to keep: raw addresses are not stored for it.
        assert status.top_clients is None

    async def test_raw_addresses_are_stored_only_for_a_material_largest_client(
        self, db_session
    ) -> None:
        settings, provider = served()
        provider.serve("SOURCE_IP", T0, [
            (*LOGIN, "198.51.100.1", 19), (*LOGIN, "198.51.100.2", 3),
            (*STATUS, "203.0.113.9", 40),
        ])
        await collect_bucket(db_session, provider, settings)

        rows = await metric_rows(db_session)
        assert settings.waf_concentration_min_client_requests == 20
        assert rows["/portal/login"].top1_client_requests == 19
        assert rows["/portal/login"].top_clients is None
        assert rows["/api/v1/status"].top_clients == [
            {"source_ip": "203.0.113.9", "requests": 40}
        ]

    async def test_no_action_response_code_or_address_value_is_persisted_verbatim(
        self, db_session
    ) -> None:
        settings, provider = served()
        provider.serve("ACTION", T0, [(*LOGIN, "QUARANTINE-7731", 100)])
        provider.serve("RESPONSE_CODE", T0, [(*LOGIN, "teapot-418", 100)])
        provider.serve("SOURCE_IP", T0, [(*LOGIN, "not-an-address", 100)])
        _, _, _, bucket = await collect_bucket(db_session, provider, settings)

        stored = json.dumps(
            [bucket.query_provenance]
            + [
                {c.name: str(getattr(m, c.name)) for c in WafEndpointMetric.__table__.columns}
                for m in (await metric_rows(db_session)).values()
            ]
        )
        for raw in ("QUARANTINE-7731", "teapot-418", "not-an-address"):
            assert raw not in stored

    async def test_each_breakdown_receives_its_mapped_property_and_the_configured_cap(
        self, db_session
    ) -> None:
        settings, provider = served(waf_max_breakdown_groups_per_bucket=77)
        _, _, _, bucket = await collect_bucket(db_session, provider, settings)

        by_kind = {c["breakdown"]: c for c in provider.breakdown_calls}
        assert set(by_kind) == {"ACTION", "RESPONSE_CODE", "SOURCE_IP"}
        assert by_kind["ACTION"]["breakdown_property"] == "WAF_Action"
        assert by_kind["RESPONSE_CODE"]["breakdown_property"] == "WAF_ResponseCode"
        assert by_kind["SOURCE_IP"]["breakdown_property"] is None
        assert {c["max_groups"] for c in provider.breakdown_calls} == {77}
        assert {c["url_property"] for c in provider.breakdown_calls} == {"URL"}
        provenance = bucket.query_provenance["breakdowns"]
        assert provenance["ACTION"] == {
            "status": "EXACT", "aql": "STUB ACTION", "groups": 3, "max_groups": 77,
            "error_class": None,
        }

    async def test_one_search_per_planned_breakdown_per_bucket(self, db_session) -> None:
        settings, provider = served()
        await collect_bucket(db_session, provider, settings)
        assert len(provider.breakdown_calls) == 3
        assert provider.observation_calls == 1


# =========================================================== failure isolation
class TestFailedBreakdown:
    @pytest.mark.parametrize(
        "error", [ProviderUnavailableError("secret query text s3cr3t"), ValueError("s3cr3t")]
    )
    async def test_a_failing_breakdown_is_recorded_failed_with_null_values_not_zero(
        self, db_session, error
    ) -> None:
        settings, provider = served()
        provider.serve("ACTION", T0, error=error)
        profile, _, report, bucket = await collect_bucket(db_session, provider, settings)

        assert bucket.action_measurement == WafTelemetryStatus.FAILED
        assert bucket.action_semantics is None
        assert bucket.action_events is None
        assert report.breakdown_failed_buckets == {"ACTION": [T0.isoformat()]}
        failed = bucket.query_provenance["breakdowns"]["ACTION"]
        assert failed["status"] == "FAILED"
        assert failed["error_class"] == type(error).__name__
        assert failed["aql"] is None and failed["groups"] is None
        # The class name only: an exception message can echo query text.
        assert "s3cr3t" not in json.dumps(bucket.query_provenance)

        for row in (await metric_rows(db_session)).values():
            assert row.action_events is None
            assert row.blocked_count is None
            assert row.unmapped_action_count is None
        # The sibling breakdowns are unaffected.
        assert bucket.response_code_measurement == WafTelemetryStatus.EXACT
        assert bucket.client_measurement == WafTelemetryStatus.EXACT
        assert bucket.response_code_semantics == identity("RESPONSE_CODE", profile)
        login = (await metric_rows(db_session))["/portal/login"]
        assert login.client_error_count == 20

    async def test_request_rate_collection_still_succeeds(self, db_session) -> None:
        settings, provider = served()
        for kind in ("ACTION", "RESPONSE_CODE", "SOURCE_IP"):
            provider.serve(kind, T0, error=ProviderUnavailableError("down"))
        _, _, report, bucket = await collect_bucket(db_session, provider, settings)

        assert bucket is not None
        assert bucket.request_count_measurement == WafMeasurement.EXACT
        assert bucket.completeness == BucketCompleteness.COMPLETE
        assert bucket.metric_semantics is not None
        assert report.intervals_collected == 1
        assert report.watermark_at == T0
        assert {k: len(v) for k, v in report.breakdown_failed_buckets.items()} == {
            "ACTION": 1, "RESPONSE_CODE": 1, "SOURCE_IP": 1,
        }
        for column in ("action", "response_code", "client"):
            assert getattr(bucket, f"{column}_measurement") == WafTelemetryStatus.FAILED
            assert getattr(bucket, f"{column}_semantics") is None
            assert getattr(bucket, f"{column}_events") is None
        rows = await metric_rows(db_session)
        assert {p: r.request_count for p, r in rows.items()} == {
            "/portal/login": 100, "/api/v1/status": 50,
        }
        for row in rows.values():
            assert row.blocked_count is None and row.client_error_count is None
            assert row.top1_client_requests is None and row.top_clients is None
            assert row.client_sources is None and row.server_error_count is None


# ============================================================ truncation
class TestTruncatedBreakdown:
    async def test_a_truncated_breakdown_is_a_lower_bound_of_its_visible_part(
        self, db_session
    ) -> None:
        settings, provider = served()
        provider.serve("RESPONSE_CODE", T0, [(*LOGIN, "500", 12), (*LOGIN, "200", 3)],
                       truncated=True)
        profile, _, report, bucket = await collect_bucket(db_session, provider, settings)

        assert bucket.response_code_measurement == WafTelemetryStatus.LOWER_BOUND
        assert bucket.response_code_semantics == identity("RESPONSE_CODE", profile)
        assert bucket.response_code_events == 15
        assert report.breakdown_lower_bound_buckets == {"RESPONSE_CODE": [T0.isoformat()]}
        assert report.breakdown_failed_buckets == {}
        assert bucket.query_provenance["breakdowns"]["RESPONSE_CODE"]["status"] == "LOWER_BOUND"
        login = (await metric_rows(db_session))["/portal/login"]
        assert (login.response_code_events, login.server_error_count) == (15, 12)
        # Independent searches, independent completeness.
        assert bucket.action_measurement == WafTelemetryStatus.EXACT
        assert bucket.client_measurement == WafTelemetryStatus.EXACT
        assert bucket.request_count_measurement == WafMeasurement.EXACT

    async def test_every_breakdown_can_be_truncated_without_touching_request_count(
        self, db_session
    ) -> None:
        settings, provider = served()
        provider.serve("ACTION", T0, ACTIONS, truncated=True)
        provider.serve("RESPONSE_CODE", T0, CODES, truncated=True)
        provider.serve("SOURCE_IP", T0, CLIENTS, truncated=True)
        _, _, _, bucket = await collect_bucket(db_session, provider, settings)

        for column in ("action", "response_code", "client"):
            assert getattr(bucket, f"{column}_measurement") == WafTelemetryStatus.LOWER_BOUND
        assert bucket.request_count_measurement == WafMeasurement.EXACT
        assert bucket.completeness == BucketCompleteness.COMPLETE

    async def test_a_truncated_volume_search_does_not_make_an_exact_breakdown_a_bound(
        self, db_session
    ) -> None:
        settings, _ = served()
        provider = BreakdownProvider([batch(T0, VOLUME, total=150, truncated=True)])
        provider.serve("ACTION", T0, ACTIONS)
        provider.serve("RESPONSE_CODE", T0, CODES)
        provider.serve("SOURCE_IP", T0, CLIENTS)
        _, _, _, bucket = await collect_bucket(db_session, provider, settings)

        assert bucket.request_count_measurement == WafMeasurement.LOWER_BOUND
        assert bucket.action_measurement == WafTelemetryStatus.EXACT
        assert bucket.client_measurement == WafTelemetryStatus.EXACT


# ======================================================= settings off / plan
class TestNotCollected:
    async def test_with_both_settings_off_nothing_is_searched_and_every_value_is_null(
        self, db_session
    ) -> None:
        settings, provider = served(
            waf_outcome_collection_enabled=False, waf_client_collection_enabled=False
        )
        _, plan, report, bucket = await collect_bucket(db_session, provider, settings)

        assert plan.collect == frozenset()
        assert provider.breakdown_calls == []
        for column in ("action", "response_code", "client"):
            assert getattr(bucket, f"{column}_measurement") == WafTelemetryStatus.NOT_COLLECTED
            assert getattr(bucket, f"{column}_semantics") is None
            assert getattr(bucket, f"{column}_events") is None
        assert bucket.query_provenance["breakdowns"] == {}
        assert report.ariel_searches == 0 and provider.observation_calls == 1
        assert bucket.request_count_measurement == WafMeasurement.EXACT
        for row in (await metric_rows(db_session)).values():
            for name in (
                "action_events", "blocked_count", "unmapped_action_count",
                "response_code_events", "client_error_count", "server_error_count",
                "unclassified_response_count", "client_events", "top1_client_requests",
                "top5_client_requests", "client_sources", "top_clients",
            ):
                assert getattr(row, name) is None, name

    async def test_the_default_settings_collect_no_breakdown(self, db_session) -> None:
        settings = cfg()
        assert not settings.waf_outcome_collection_enabled
        assert not settings.waf_client_collection_enabled
        provider = BreakdownProvider([batch(T0, VOLUME)])
        _, _, _, bucket = await collect_bucket(db_session, provider, settings)

        assert provider.breakdown_calls == []
        assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED

    async def test_each_setting_gates_only_its_own_breakdowns(self, db_session) -> None:
        settings, provider = served(waf_outcome_collection_enabled=False)
        _, plan, _, bucket = await collect_bucket(db_session, provider, settings)

        assert plan.collect == {WafBreakdown.SOURCE_IP}
        assert {c["breakdown"] for c in provider.breakdown_calls} == {"SOURCE_IP"}
        assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED
        assert bucket.response_code_measurement == WafTelemetryStatus.NOT_COLLECTED
        assert bucket.client_measurement == WafTelemetryStatus.EXACT
        login = (await metric_rows(db_session))["/portal/login"]
        assert login.blocked_count is None and login.client_events == 100

    async def test_a_collector_without_a_plan_is_the_two_search_collection(
        self, db_session
    ) -> None:
        settings, provider = served()
        profile, source = await source_with_profile(
            db_session, provider, settings, mapping=AUTHORED
        )
        instance = await db_session.get(QRadarInstance, source.instance_id)
        collector = WafEndpointCollector(
            db_session, provider, mapping=WafFieldMapping.from_profile(profile),
            profile_version=profile.profile_version, settings=settings,
            clock=lambda: T0 + STEP + timedelta(seconds=5),
        )
        await collector.collect(instance, log_source=source)

        bucket = await db_session.get(WafSourceBucket, (source.id, T0))
        assert provider.breakdown_calls == []
        assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED
        assert bucket.client_measurement == WafTelemetryStatus.NOT_COLLECTED


class TestActionNeedsAnAuthoredMap:
    async def test_no_action_map_means_no_action_breakdown(self, db_session) -> None:
        settings, provider = served()
        _, plan, _, bucket = await collect_bucket(
            db_session, provider, settings, mapping=LAB_MAPPING
        )

        assert WafBreakdown.ACTION not in plan.collect
        assert "BLOCKED" in plan.skipped[WafBreakdown.ACTION]
        assert "ACTION" not in {c["breakdown"] for c in provider.breakdown_calls}
        assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED
        assert bucket.action_semantics is None and bucket.action_events is None
        # The other breakdowns are not held back by it.
        assert bucket.response_code_measurement == WafTelemetryStatus.EXACT
        assert bucket.client_measurement == WafTelemetryStatus.EXACT
        for row in (await metric_rows(db_session)).values():
            assert row.blocked_count is None and row.action_events is None

    async def test_an_allowed_only_map_names_no_blocked_value_and_is_refused(
        self, db_session
    ) -> None:
        allowed_only = WafFieldMapping(
            url="URL", method="HTTP_Method", action="WAF_Action", response_code="WAF_ResponseCode",
            allowed_actions=("ALLOW", "DENY"),
        )
        settings, provider = served()
        _, plan, _, bucket = await collect_bucket(
            db_session, provider, settings, mapping=allowed_only
        )
        assert WafBreakdown.ACTION not in plan.collect
        assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED

    async def test_a_map_already_known_to_be_stale_is_refused(self, db_session) -> None:
        # Validation discovers ALLOW and DENY on the lab source; this map omits ALLOW.
        stale = WafFieldMapping(
            url="URL", method="HTTP_Method", action="WAF_Action", response_code="WAF_ResponseCode",
            blocked_actions=("DENY",),
        )
        settings, provider = served()
        _, plan, _, bucket = await collect_bucket(db_session, provider, settings, mapping=stale)

        assert WafBreakdown.ACTION not in plan.collect
        assert "ALLOW" in plan.skipped[WafBreakdown.ACTION]
        assert bucket.action_measurement == WafTelemetryStatus.NOT_COLLECTED
        assert "ACTION" not in {c["breakdown"] for c in provider.breakdown_calls}

    async def test_an_unmapped_response_code_property_skips_only_that_breakdown(
        self, db_session
    ) -> None:
        no_codes = WafFieldMapping(
            url="URL", method="HTTP_Method", action="WAF_Action",
            blocked_actions=("DENY",), allowed_actions=("ALLOW",),
        )
        settings, provider = served()
        _, plan, _, bucket = await collect_bucket(db_session, provider, settings, mapping=no_codes)

        assert WafBreakdown.RESPONSE_CODE not in plan.collect
        assert bucket.response_code_measurement == WafTelemetryStatus.NOT_COLLECTED
        assert bucket.action_measurement == WafTelemetryStatus.EXACT


# ======================================================== rows from breakdowns
class TestEndpointMentionedOnlyByABreakdown:
    async def _two_buckets(self, session, provider, settings):  # type: ignore[no-untyped-def]
        _profile, source, instance, _plan, collector = await setup(
            session, provider, settings
        )
        await collector(T0 + STEP + timedelta(seconds=5)).collect(instance, log_source=source)
        report = await collector(T0 + STEP * 2 + timedelta(seconds=5)).collect(
            instance, log_source=source
        )
        return source, report

    async def test_a_tracked_endpoint_the_volume_search_missed_still_gets_a_row(
        self, db_session
    ) -> None:
        settings, provider = served()
        second = T0 + STEP
        # Bucket 2: the volume search names only /status (and was cut before the
        # login endpoint), yet the breakdowns saw /portal/login.
        provider.batches[second] = batch(second, [(*STATUS, 50)], total=150, truncated=True)
        provider.serve("ACTION", second, [(*LOGIN, "DENY", 9), (*STATUS, "ALLOW", 50)])
        provider.serve("RESPONSE_CODE", second, [(*LOGIN, "404", 9)])
        provider.serve("SOURCE_IP", second, [(*LOGIN, "198.51.100.1", 25)])
        source, report = await self._two_buckets(db_session, provider, settings)

        assert report.intervals_collected == 1
        login_id = await db_session.scalar(
            select(WafEndpoint.id).where(WafEndpoint.normalized_endpoint == "/portal/login")
        )
        row = await db_session.get(WafEndpointMetric, (login_id, second))
        assert row is not None
        # Its visible volume is zero; its breakdown counts are kept.
        assert row.request_count == 0
        assert (row.action_events, row.blocked_count) == (9, 9)
        assert (row.response_code_events, row.client_error_count) == (9, 9)
        assert (row.client_events, row.top1_client_requests) == (25, 25)
        assert row.top_clients == [{"source_ip": "198.51.100.1", "requests": 25}]
        bucket = await db_session.get(WafSourceBucket, (source.id, second))
        assert bucket.request_count_measurement == WafMeasurement.LOWER_BOUND
        assert bucket.action_measurement == WafTelemetryStatus.EXACT

    async def test_an_endpoint_no_breakdown_mentions_gets_no_row_in_a_later_bucket(
        self, db_session
    ) -> None:
        settings, provider = served()
        second = T0 + STEP
        provider.batches[second] = batch(second, [], total=0)
        await self._two_buckets(db_session, provider, settings)

        rows = (await db_session.scalars(
            select(WafEndpointMetric).where(WafEndpointMetric.bucket_start == second)
        )).all()
        assert rows == []

    async def test_a_breakdown_cannot_admit_an_untracked_endpoint(self, db_session) -> None:
        settings, provider = served()
        provider.serve("ACTION", T0, [*ACTIONS, ("/ghost/path", "GET", "DENY", 500)])
        provider.serve("SOURCE_IP", T0, [*CLIENTS, ("/ghost/path", "GET", "192.0.2.1", 500)])
        await collect_bucket(db_session, provider, settings)

        names = set((await db_session.scalars(select(WafEndpoint.normalized_endpoint))).all())
        assert names == {"/portal/login", "/api/v1/status"}
        assert set(await metric_rows(db_session)) == names


# ====================================== values that must not cost the bucket
class TestOddBreakdownValues:
    async def test_a_response_code_python_cannot_int_does_not_fail_the_bucket(
        self, db_session
    ) -> None:
        settings, provider = served()
        provider.serve(
            "RESPONSE_CODE", T0, [(*LOGIN, "\u00b2", 8), (*LOGIN, "403", 20), (*LOGIN, "200", 72)]
        )
        _, _, report, bucket = await collect_bucket(db_session, provider, settings)

        assert report.intervals_collected == 1
        assert bucket is not None
        assert bucket.request_count_measurement == WafMeasurement.EXACT
        assert bucket.response_code_measurement == WafTelemetryStatus.EXACT
        login = (await metric_rows(db_session))["/portal/login"]
        assert login.request_count == 100
        assert (login.response_code_events, login.client_error_count,
                login.unclassified_response_count) == (100, 20, 8)

    async def test_unparseable_source_values_lower_coverage_and_are_recorded(
        self, db_session
    ) -> None:
        settings, provider = served()
        provider.serve(
            "SOURCE_IP", T0, [(*LOGIN, "garbage", 60), (*LOGIN, "198.51.100.1", 40)]
        )
        _, _, _, bucket = await collect_bucket(db_session, provider, settings)

        login = (await metric_rows(db_session))["/portal/login"]
        assert (login.client_events, login.top1_client_requests, login.client_sources) == (
            40, 40, 1
        )
        assert [c["source_ip"] for c in login.top_clients or []] == ["198.51.100.1"]
        recorded = bucket.query_provenance["breakdowns"]["SOURCE_IP"]
        assert recorded["unattributed_requests"] == 60
