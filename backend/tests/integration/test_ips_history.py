"""HB-7: the IPS history adapter — its guards, its reading and its hand-off.

The adapter reads QRadar's past for a source that has no live bucket yet, writes the
rows as HISTORY, and hands the source back to the live collector with no gap and no
overlap. The tests drive the collector and the pipeline: the operator's path.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update

from app.collectors import ips_collector as ips_collector_module
from app.collectors.ips_collector import IpsCollector, watermark_collector
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.custom_rules.engine import CustomRuleEngine
from app.custom_rules.preview import replay
from app.history.progress_codec import decode_progress, encode_progress
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.enums import (
    AnomalyState,
    CustomRuleMetric,
    CustomRuleSubjectKind,
    IpsDimension,
    SearchFamily,
    Severity,
)
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope
from app.models.instance import QRadarInstance
from app.models.ips import (
    IpsAnomaly,
    IpsDetectorState,
    IpsEntity,
    IpsEntityMetric,
    IpsSourceBucket,
)
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider
from app.services.ariel_permit import _ARIEL_PRIORITY, ArielPermitUnavailable
from app.services.ips_pipeline import (
    IpsInstancePipeline,
    evaluation_collector,
    ips_pipeline_lock_key,
)
from app.services.locks import collector_lock_key
from app.services.search_cadence import SearchCadenceService
from tests.integration.factories import make_instance
from tests.integration.test_history_backfill_api import READER, Client
from tests.integration.test_ips_baseline import add_bucket as ips_bucket
from tests.integration.test_ips_baseline import add_source as ips_source
from tests.integration.test_ips_baseline import settings as ips_settings
from tests.integration.test_ips_collection import add_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: A collection tick: 10:07 with a five-minute bucket, so the boundary is 10:05.
NOW = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
BUCKET = timedelta(minutes=5)
#: The smallest window the platform allows (§B.6.5).
WINDOW_DAYS = 7
#: The floor margin: the read starts one day before the asked window (§B.3.2).
MARGIN = timedelta(hours=24)
#: The QRadar source id of the test's one source.
SOURCE_ID = 412


def hcfg(**overrides: object) -> Settings:
    """The IPS settings a history tick runs with."""
    values = get_settings().model_dump()
    values.update(
        ips_collection_enabled=True,
        collection_interval_seconds=300,
        collection_max_backfill_intervals=0,
        search_cadence_max_searches_per_tick=64,
        ariel_multi_bucket_max_rows=5000,
        ips_max_entities_per_bucket=200,
        ips_max_new_entities_per_bucket=20,
        ips_max_active_entities_per_source_dimension=2000,
        ips_min_admission_events=3,
        history_backfill_enabled=True,
        history_backfill_days=WINDOW_DAYS,
        history_backfill_searches_per_tick=64,
        history_backfill_seconds_per_tick=120,
        history_backfill_floor_margin_hours=24,
    )
    values.update(overrides)
    return Settings(**values)


def boundary_of(now: datetime) -> datetime:
    """The closed-bucket boundary of a tick."""
    return floor_to_interval(now, int(BUCKET.total_seconds()))


def buckets(start: datetime, end: datetime) -> list[datetime]:
    """The whole bucket starts in `[start, end)`."""
    out: list[datetime] = []
    cursor = start
    while cursor < end:
        out.append(cursor)
        cursor += BUCKET
    return out


class ScriptedIpsProvider(MockQRadarProvider):
    """The mock's own aggregation and parsing, over the events the test writes.

    `events` maps a bucket start to the events of that bucket. A bucket that is not
    in the map is silent, which is a measured silence and never a zero.
    """

    def __init__(self, events: dict[datetime, list[dict]], *, now: datetime) -> None:
        super().__init__(now=now)
        self.events = events

    def _ips_events(self, source_id: int, key: int) -> list[dict]:
        start = datetime.fromtimestamp(key * int(BUCKET.total_seconds()), tz=UTC)
        return [dict(event) for event in self.events.get(start, ())]


def one_event(events: int = 5) -> dict:
    """One synthetic IPS event with enough volume to be admitted."""
    return {
        "sip": "192.0.2.10",
        "dip": "198.51.100.20",
        "dport": 443,
        "proto": 6,
        "qid": 5_000_123,
        "qname": "IPS signature 5000123",
        "sig": "SIG-1",
        "act": "deny",
        "events": events,
        "records": events,
    }


def window_events(start: datetime, end: datetime, *, events: int = 5) -> dict[datetime, list[dict]]:
    """One event in every bucket of `[start, end)`."""
    return {at: [one_event(events)] for at in buckets(start, end)}


async def record_yes(
    session, instance, *, days: int = WINDOW_DAYS, at: datetime = NOW
) -> None:  # type: ignore[no-untyped-def]
    """The operator's answer: Yes, read the last `days` days."""
    session.add(
        HistoryBackfillDecision(
            instance_id=instance.id,
            answer="YES",
            days=days,
            decided_at=at,
            decided_by="test",
            source="UI",
        )
    )
    await session.flush()


async def add_active_entities(session, instance_id, source_id, count: int) -> None:  # type: ignore[no-untyped-def]
    session.add_all(
        [
            IpsEntity(
                instance_id=instance_id,
                log_source_id=source_id,
                dimension=dimension,
                value_key=f"{dimension.value}-{index}",
                first_seen_at=NOW - timedelta(days=30),
                last_seen_at=NOW,
                active=True,
                normalizer_version=1,
            )
            for dimension in (
                IpsDimension.DESTINATION_PORT,
                IpsDimension.DESTINATION_IP,
                IpsDimension.SIGNATURE,
            )
            for index in range(count)
        ]
    )
    await session.flush()


async def scope_of(session, instance, key: str) -> HistoryBackfillScope | None:  # type: ignore[no-untyped-def]
    """The stored scope row, read fresh (the bulk upsert leaves the loaded one stale)."""
    return (
        await session.scalars(
            select(HistoryBackfillScope)
            .where(
                HistoryBackfillScope.instance_id == instance.id,
                HistoryBackfillScope.family == "IPS",
                HistoryBackfillScope.scope_key == key,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def collect(  # type: ignore[no-untyped-def]
    session,
    instance,
    provider,
    settings,
    now: datetime,
):
    """One collection tick, committed so the next tick sees it."""
    report = await IpsCollector(
        session, provider, settings=settings, clock=lambda: now
    ).collect(instance)
    await session.commit()
    return report


async def read_history(  # type: ignore[no-untyped-def]
    session,
    instance,
    provider,
    settings,
    *,
    start: datetime = NOW,
    ticks: int = 20,
    step: timedelta = timedelta(0),
    key: str = str(SOURCE_ID),
):
    """Tick until the scope leaves `READING` (or the ticks run out).

    The clock stands still by default, so the tick's boundary does not move and
    the reading covers exactly the window the test planned. The reading moves one
    chunk per tick, so a window of seven days needs a few ticks.
    """
    now = start
    reports = []
    for _ in range(ticks):
        reports.append(await collect(session, instance, provider, settings, now))
        scope = await scope_of(session, instance, key)
        if scope is not None and scope.state != "READING":
            break
        now += step
    return reports, now


async def watermark_of(  # type: ignore[no-untyped-def]
    session, instance, collector: str
) -> CollectionWatermark | None:
    return (
        await session.scalars(
            select(CollectionWatermark)
            .where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == collector,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


def f1_settings(**overrides: object) -> Settings:
    """The custom-rule settings a history guard test runs with."""
    values = get_settings().model_dump()
    values.update(
        custom_rules_enabled=True,
        ips_collection_enabled=True,
        collection_interval_seconds=3600,
    )
    values.update(overrides)
    return Settings(**values)


async def arm_f1(session, kind: CustomRuleSubjectKind, through: datetime) -> None:  # type: ignore[no-untyped-def]
    """The F.1 watermark just before the data, so the first run judges."""
    await session.execute(delete(CustomRuleWatermark))
    session.add(
        CustomRuleWatermark(
            subject_kind=kind,
            evaluated_through=through,
            updated_at=through,
        )
    )
    await session.flush()


class TestReading:
    """A source with no live bucket reads its past and writes it as HISTORY."""

    async def test_a_new_source_with_a_yes_reads_its_history(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )

        reports, last = await read_history(db_session, instance, provider, hcfg())

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "COMPLETE"
        assert scope.days_target == WINDOW_DAYS
        assert scope.target_start == target
        assert "fitted with no admitted entity yet" in (scope.reason or "")
        assert scope.floor == target
        assert scope.progress  # the scope has searched
        # The reading ends at the boundary of the tick that finished it.
        end = boundary_of(last)
        assert scope.anchor == end
        written = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert written
        assert all(row.origin == "HISTORY" for row in written)
        assert min(row.bucket_start for row in written) == target
        assert max(row.bucket_start for row in written) == end - BUCKET
        # The reading is paused: the source has no live bucket yet.
        live = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "LIVE",
                )
            )
        ).all()
        assert live == []
        assert reports[0].history_sources == 1

    async def test_a_history_row_says_where_it_came_from(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await read_history(db_session, instance, provider, hcfg())

        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(IpsSourceBucket.log_source_id == source.id)
            )
        ).all()
        assert rows
        assert {row.origin for row in rows} == {"HISTORY"}
        # The provenance says where the row came from, and the profile version is
        # the current one (A5).
        assert all(row.query_provenance.get("history") is True for row in rows)
        assert all("floor" in row.query_provenance for row in rows)
        assert {row.profile_version for row in rows} == {1}

    async def test_the_admitted_entities_are_written_with_their_metrics(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await read_history(db_session, instance, provider, hcfg())

        entities = (
            await db_session.scalars(
                select(IpsEntity).where(IpsEntity.log_source_id == source.id)
            )
        ).all()
        assert {entity.dimension for entity in entities} == {
            "DESTINATION_PORT",
            "DESTINATION_IP",
            "SIGNATURE",
        }
        # The first admission is the first history bucket, not the collection time.
        assert min(entity.first_seen_at for entity in entities) == target
        metrics = (
            await db_session.scalars(
                select(IpsEntityMetric).where(
                    IpsEntityMetric.ips_entity_id.in_([e.id for e in entities])
                )
            )
        ).all()
        assert metrics
        assert all(metric.events == 5 for metric in metrics)

    async def test_a_source_with_live_buckets_is_not_eligible(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        provider = ScriptedIpsProvider({}, now=NOW)
        # A first collection writes a live bucket: the source has collected.
        await collect(db_session, instance, provider, hcfg(), NOW)
        live = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "LIVE",
                )
            )
        ).all()
        assert live

        await record_yes(db_session, instance)
        await collect(db_session, instance, provider, hcfg(), NOW + BUCKET)

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "NOT_ELIGIBLE"
        assert "already collected live data" in (scope.reason or "")
        # Nothing was read for it.
        assert scope.floor is None and scope.through is None
        assert scope.progress == {}

    async def test_a_console_without_a_yes_creates_no_scope(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        provider = ScriptedIpsProvider({}, now=NOW)

        await collect(db_session, instance, provider, hcfg(), NOW)

        assert await scope_of(db_session, instance, str(SOURCE_ID)) is None

    async def test_ips_fits_once_and_the_estimate_route_matches_the_adapter(
        self, db_session, db_schema
    ) -> None:
        settings = hcfg(
            history_backfill_days=28,
            history_backfill_max_hours=8,
            history_backfill_searches_per_tick=1,
        )
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await add_active_entities(db_session, instance.id, source.id, 30)
        await record_yes(db_session, instance, days=28)
        await SearchCadenceService(db_session, settings).set_cadence(
            SearchFamily.IPS_COLLECTION, 300, actor="test", source_ip=None
        )
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=7)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await db_session.commit()

        await collect(db_session, instance, provider, settings, NOW)
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.days_target == 7 and scope.target_start == target
        assert "window reduced from 28 to 7 days" in (scope.reason or "")
        # One search cannot fund a seven-dimension read chunk.  The fit is stored
        # nevertheless; a later tick with room for a chunk must use that same fit.
        assert provider.ips_searches == []

        reading_settings = settings.model_copy(
            update={"history_backfill_searches_per_tick": 7}
        )
        await collect(db_session, instance, provider, reading_settings, NOW)
        assert provider.ips_searches[0].window_start == target - MARGIN
        starts = (
            await db_session.scalars(
                select(IpsSourceBucket.bucket_start).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert not starts or min(starts) >= target

        route = Client(
            db_schema,
            READER,
            settings_update={
                "ips_collection_enabled": True,
                "history_backfill_days": 28,
                "history_backfill_max_hours": 8,
                "history_backfill_searches_per_tick": 1,
            },
        )
        estimates = {
            item["family"]: item
            for item in route.get("/api/v1/admin/history-backfill/estimate").json()[0][
                "families"
            ]
        }
        assert estimates["IPS"]["days_fitted"] == scope.days_target

        # The stored plan wins after settings and counts change.
        calls = len(provider.ips_searches)
        changed = reading_settings.model_copy(
            update={
                "history_backfill_max_hours": 1,
                "history_backfill_searches_per_tick": 64,
            }
        )
        await collect(db_session, instance, provider, changed, NOW)
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.days_target == 7 and scope.target_start == target
        assert "window reduced from 28 to 7 days" in (scope.reason or "")
        assert len(provider.ips_searches) > calls
        assert scope.state == "COMPLETE"

    async def test_ips_refuses_below_seven_days_without_an_ariel_search(
        self, db_session
    ) -> None:
        settings = hcfg(
            history_backfill_days=28,
            history_backfill_max_hours=1,
            history_backfill_searches_per_tick=1,
        )
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await add_active_entities(db_session, instance.id, source.id, 30)
        await record_yes(db_session, instance, days=28)
        await SearchCadenceService(db_session, settings).set_cadence(
            SearchFamily.IPS_COLLECTION, 300, actor="test", source_ip=None
        )
        provider = ScriptedIpsProvider({}, now=NOW)
        await db_session.commit()

        report = await collect(db_session, instance, provider, settings, NOW)
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None and scope.state == "UNAVAILABLE"
        assert "even 7 days" in (scope.reason or "")
        assert report.history_searches == 0 and provider.ips_searches == []
        collection = await watermark_of(
            db_session, instance, watermark_collector(SOURCE_ID)
        )
        evaluation = await watermark_of(
            db_session, instance, evaluation_collector(SOURCE_ID)
        )
        assert collection is not None and collection.watermark_at is None
        assert evaluation is not None and evaluation.watermark_at is None

    async def test_ips_estimate_and_adapters_use_the_same_fair_share(
        self, db_session, db_schema
    ) -> None:
        settings = hcfg(
            history_backfill_days=28,
            history_backfill_max_hours=4,
            history_backfill_searches_per_tick=10,
        )
        instance = await make_instance(db_session)
        for source_id in (500, 501):
            _, source = await add_source(db_session, instance, source_id)
            await add_active_entities(db_session, instance.id, source.id, 30)
        await record_yes(db_session, instance, days=28)
        await SearchCadenceService(db_session, settings).set_cadence(
            SearchFamily.IPS_COLLECTION, 300, actor="test", source_ip=None
        )
        await db_session.commit()

        await collect(db_session, instance, ScriptedIpsProvider({}, now=NOW), settings, NOW)
        scopes = [
            await scope_of(db_session, instance, str(source_id))
            for source_id in (500, 501)
        ]
        assert all(scope is not None and scope.days_target == 20 for scope in scopes)
        assert all("fair share of 5 of the 10" in (scope.reason or "") for scope in scopes)

        route = Client(
            db_schema,
            READER,
            settings_update={
                "ips_collection_enabled": True,
                "history_backfill_days": 28,
                "history_backfill_max_hours": 4,
                "history_backfill_searches_per_tick": 10,
            },
        )
        ips = next(
            item
            for item in route.get("/api/v1/admin/history-backfill/estimate").json()[0][
                "families"
            ]
            if item["family"] == "IPS"
        )
        assert ips["days_fitted"] == 20
        assert "fair share of 5 of the 10" in " ".join(ips["assumptions"])


class TestFloor:
    """The floor is proved from the planner's scan span, not from the asked start."""

    async def test_a_console_that_holds_more_than_the_window_reads_the_whole_window(
        self, db_session
    ) -> None:
        """Forty days of events and a 28-day request: the floor is the asked start.

        The planner scans from `target_start - margin`, so on a console that holds
        more than the asked window the first event of the scan is the margin's
        first bucket and the floor is the asked start itself (§B.3.2, §B.3.4).
        The reading therefore ends COMPLETE and holds the whole asked window.
        """
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance, days=28)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=28)
        # The console holds forty days: its events start twelve days before the
        # asked window, so the scan finds an event in its very first bucket.
        provider = ScriptedIpsProvider(
            window_events(target - timedelta(days=12), boundary), now=NOW
        )
        settings = hcfg(history_backfill_days=28)

        _reports, last = await read_history(db_session, instance, provider, settings)

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.days_target == 28
        assert scope.target_start == target
        # The floor is the asked start, not a few buckets later: the console holds
        # more than was asked, so nothing was cut.
        assert scope.floor == target
        assert scope.state == "COMPLETE"
        assert "holds less than asked" not in (scope.reason or "")
        # The whole asked window was read.
        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.origin == "HISTORY",
                    IpsSourceBucket.log_source_id == source.id,
                )
            )
        ).all()
        assert rows
        assert min(row.bucket_start for row in rows) == target
        assert max(row.bucket_start for row in rows) == boundary_of(last) - BUCKET


class TestHandOff:
    """The hand-off leaves no gap and no overlap."""

    async def test_the_hand_off_seeds_both_watermarks(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        _reports, last = await read_history(db_session, instance, provider, hcfg())
        end = boundary_of(last)

        collection = await watermark_of(
            db_session, instance, watermark_collector(SOURCE_ID)
        )
        evaluation = await watermark_of(
            db_session, instance, evaluation_collector(SOURCE_ID)
        )
        assert collection is not None and evaluation is not None
        # Both watermarks sit on the last history bucket: the live collector starts
        # at the boundary, so there is no gap and no overlap.
        assert collection.watermark_at == end - BUCKET
        assert evaluation.watermark_at == end - BUCKET

    async def test_the_live_collection_resumes_at_the_boundary(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        _reports, last = await read_history(db_session, instance, provider, hcfg())
        end = boundary_of(last)

        # The next tick collects the bucket at the boundary, and nothing before it.
        later = last + BUCKET
        await collect(db_session, instance, provider, hcfg(), later)

        live = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "LIVE",
                )
            )
        ).all()
        assert [row.bucket_start for row in live] == [end]
        history = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert max(row.bucket_start for row in history) == end - BUCKET

    async def test_nothing_settled_leaves_the_watermarks_null(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        # No event at all: the scope is unavailable and nothing was settled.
        provider = ScriptedIpsProvider({}, now=NOW)
        _reports, _last = await read_history(db_session, instance, provider, hcfg())

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "UNAVAILABLE"
        assert scope.through is None
        collection = await watermark_of(
            db_session, instance, watermark_collector(SOURCE_ID)
        )
        evaluation = await watermark_of(
            db_session, instance, evaluation_collector(SOURCE_ID)
        )
        assert collection is not None and collection.watermark_at is None
        assert evaluation is not None and evaluation.watermark_at is None

    async def test_the_switch_off_ends_the_reading_in_the_same_tick(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await read_history(db_session, instance, provider, hcfg(), ticks=1)
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None and scope.state == "READING"

        settings = hcfg(history_backfill_enabled=False)
        await collect(db_session, instance, provider, settings, NOW + BUCKET)

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "PARTIAL"
        assert "switched off" in (scope.reason or "")

    async def test_a_stop_request_ends_the_reading_in_the_same_tick(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await read_history(db_session, instance, provider, hcfg(), ticks=1)

        decision = await db_session.get(HistoryBackfillDecision, instance.id)
        assert decision is not None
        decision.stop_requested_at = NOW + BUCKET
        decision.stop_requested_by = "operator"
        await db_session.flush()
        await collect(db_session, instance, provider, hcfg(), NOW + BUCKET)

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "PARTIAL"
        assert "stopped by operator" in (scope.reason or "")


class TestStatementCount:
    """A history tick costs the same statements whatever it reads."""

    @staticmethod
    def _counting(session):  # type: ignore[no-untyped-def]
        from tests.integration.test_ips_collection import counting

        return counting(session)

    async def test_the_count_is_equal_for_one_and_ten_reading_sources(
        self, db_session
    ) -> None:
        """Starting adds one bulk count; later work stays constant at fleet scale."""
        first_counts = {}
        later_counts = {}
        for sources in (1, 10):
            instance = await make_instance(db_session)
            source_ids = []
            for index in range(sources):
                _profile, source = await add_source(db_session, instance, 500 + index)
                source_ids.append(source.id)
            await record_yes(db_session, instance)
            boundary = boundary_of(NOW)
            target = boundary - timedelta(days=WINDOW_DAYS)
            provider = ScriptedIpsProvider(
                window_events(target - MARGIN, boundary), now=NOW
            )
            # The first tick creates the scopes, counts every source in one statement,
            # and proves the floors.
            with self._counting(db_session) as first:
                await collect(db_session, instance, provider, hcfg(), NOW)
            first_counts[sources] = len(first)
            entity_counts = [
                sql
                for sql in first
                if "count(ips_entity.id)" in sql.lower()
                and "group by ips_entity.log_source_id" in sql.lower()
            ]
            assert len(entity_counts) == 1
            # The second tick reads and writes: one statement per table for the
            # whole fleet, whatever the fleet's size.
            with self._counting(db_session) as statements:
                await collect(db_session, instance, provider, hcfg(), NOW)
            later_counts[sources] = len(statements)
            assert not [sql for sql in statements if "count(ips_entity.id)" in sql.lower()]
            rows = (
                await db_session.scalars(
                    select(IpsSourceBucket).where(
                        IpsSourceBucket.log_source_id.in_(source_ids),
                        IpsSourceBucket.origin == "HISTORY",
                    )
                )
            ).all()
            assert rows, "the counted tick wrote no history row"
        assert first_counts == {1: 13, 10: 13}
        assert later_counts == {1: 16, 10: 16}

    async def test_three_failed_sources_write_scope_errors_in_one_statement(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        source_ids = (500, 501, 502)
        for source_id in source_ids:
            await add_source(db_session, instance, source_id)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        provider = ScriptedIpsProvider(
            window_events(boundary - timedelta(days=WINDOW_DAYS) - MARGIN, boundary),
            now=NOW,
        )
        provider.ips_fault = lambda search: (
            ProviderError("SELECT sensitive_property FROM events")
            if search.window_end < boundary
            else None
        )

        with self._counting(db_session) as statements:
            await collect(db_session, instance, provider, hcfg(), NOW)

        error_writes = [
            sql
            for sql in statements
            if sql.lstrip().startswith("UPDATE history_backfill_scope")
            and "last_error_class" in sql
        ]
        assert len(error_writes) == 1
        for source_id in source_ids:
            row = await scope_of(db_session, instance, str(source_id))
            assert row is not None and row.state == "READING"
            assert row.last_error_class == "ProviderError" and row.last_error_at == NOW

    async def test_the_count_does_not_grow_with_the_span(self, db_session) -> None:
        """A twelve-bucket step and a 2,016-bucket step cost the same statements."""
        counts = {}
        for buckets_written in (12, 2016):
            instance = await make_instance(db_session)
            _, source = await add_source(db_session, instance, SOURCE_ID)
            await record_yes(db_session, instance)
            boundary = boundary_of(NOW)
            target = boundary - timedelta(days=WINDOW_DAYS)
            if buckets_written == 12:
                # A young source: the floor is an hour before the boundary, so the
                # reading is twelve buckets long.
                start = boundary - MARGIN - BUCKET * buckets_written
            else:
                start = target - MARGIN
            provider = ScriptedIpsProvider(window_events(start, boundary), now=NOW)
            with self._counting(db_session) as statements:
                await read_history(db_session, instance, provider, hcfg())
            rows = (
                await db_session.scalars(
                    select(IpsSourceBucket).where(
                        IpsSourceBucket.log_source_id == source.id,
                        IpsSourceBucket.origin == "HISTORY",
                    )
                )
            ).all()
            assert len(rows) == buckets_written, len(rows)
            counts[buckets_written] = len(statements)
        assert counts[12] == counts[2016], counts
        # Pinned: the whole reading of one source, scan tick and reading tick.
        assert counts[12] == 31, counts

    async def test_the_count_does_not_grow_with_the_number_of_entities(
        self, db_session
    ) -> None:
        """One entity per bucket and twenty-two cost the same statements."""
        counts = {}
        for entities in (1, 22):
            instance = await make_instance(db_session)
            _, source = await add_source(db_session, instance, SOURCE_ID)
            await record_yes(db_session, instance)
            boundary = boundary_of(NOW)
            # A young source, so the reading is twelve buckets long either way.
            start = boundary - MARGIN - BUCKET * 12
            events = {}
            for at in buckets(start, boundary):
                events[at] = [
                    {
                        "sip": "192.0.2.10",
                        "dip": "198.51.100.20",
                        "dport": 443 if entities == 1 else 1000 + index,
                        "proto": 6,
                        "qid": 5_000_123,
                        "qname": "IPS signature 5000123",
                        "sig": "SIG-1",
                        "act": "deny",
                        "events": 5,
                        "records": 5,
                    }
                    for index in range(entities)
                ]
            provider = ScriptedIpsProvider(events, now=NOW)
            with self._counting(db_session) as statements:
                await read_history(db_session, instance, provider, hcfg())
            rows = (
                await db_session.scalars(
                    select(IpsSourceBucket).where(
                        IpsSourceBucket.log_source_id == source.id,
                        IpsSourceBucket.origin == "HISTORY",
                    )
                )
            ).all()
            assert len(rows) == 12, len(rows)
            counts[entities] = len(statements)
        assert counts[1] == counts[22], counts
        # Pinned: the same reading, with one entity per bucket and with many.
        assert counts[1] == 31, counts

    async def test_a_step_writes_more_rows_than_the_bind_limit_allows(
        self, db_session
    ) -> None:
        """2,016 buckets x 50 columns is over asyncpg's 32,767 bind parameters."""
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await read_history(db_session, instance, provider, hcfg())

        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert len(rows) == 2016
        # Every row is there, with its own values.
        starts = {row.bucket_start for row in rows}
        assert len(starts) == 2016
        assert all(row.total_events == 5 for row in rows)


class TestEquivalence:
    """The history rows are the live rows, column for column."""

    async def test_the_history_rows_equal_the_live_rows(self, db_session) -> None:
        boundary = boundary_of(NOW)
        # A young source: the floor is an hour before the boundary, so the reading
        # and the live collection cover the same twelve buckets.
        floor = boundary - timedelta(hours=1)
        events = window_events(floor - MARGIN, boundary)

        # The live path: a source whose watermark sits before the window, so the
        # live collection covers the very same buckets.
        live_instance = await make_instance(db_session)
        _, live_source = await add_source(db_session, live_instance, SOURCE_ID)
        db_session.add(
            CollectionWatermark(
                instance_id=live_instance.id,
                collector=watermark_collector(SOURCE_ID),
                watermark_at=floor - BUCKET,
            )
        )
        await db_session.flush()
        live_settings = hcfg(collection_max_backfill_intervals=11)
        await collect(
            db_session,
            live_instance,
            ScriptedIpsProvider(events, now=NOW),
            live_settings,
            NOW,
        )

        # The history path: the same events, read as history.
        history_instance = await make_instance(db_session)
        await add_source(db_session, history_instance, SOURCE_ID)
        await record_yes(db_session, history_instance)
        await read_history(
            db_session,
            history_instance,
            ScriptedIpsProvider(events, now=NOW),
            hcfg(),
        )

        live_rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == live_source.id
                )
            )
        ).all()
        history_rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id != live_source.id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert len(live_rows) == len(history_rows) == 12

        ignored = {"origin", "query_provenance", "collected_at", "log_source_id"}
        live_by_start = {row.bucket_start: row for row in live_rows}
        for row in history_rows:
            other = live_by_start[row.bucket_start]
            for column in IpsSourceBucket.__table__.columns:
                if column.name in ignored:
                    continue
                assert getattr(row, column.name) == getattr(other, column.name), (
                    column.name,
                    row.bucket_start,
                )

        live_entities = {
            (entity.dimension, entity.value_key): entity
            for entity in (
                await db_session.scalars(
                    select(IpsEntity).where(
                        IpsEntity.log_source_id == live_source.id
                    )
                )
            ).all()
        }
        history_entities = {
            (entity.dimension, entity.value_key): entity
            for entity in (
                await db_session.scalars(
                    select(IpsEntity).where(
                        IpsEntity.log_source_id != live_source.id
                    )
                )
            ).all()
        }
        assert set(live_entities) == set(history_entities)
        for key, entity in live_entities.items():
            other = history_entities[key]
            for column in IpsEntity.__table__.columns:
                # The row's own bookkeeping (when it was written) is not a
                # collection fact and may differ.
                if column.name in {
                    "id",
                    "instance_id",
                    "log_source_id",
                    "created_at",
                    "updated_at",
                }:
                    continue
                assert getattr(entity, column.name) == getattr(other, column.name), (
                    key,
                    column.name,
                )

        live_metrics = {
            (entity.dimension, entity.value_key, metric.bucket_start): metric.events
            for entity in live_entities.values()
            for metric in (
                await db_session.scalars(
                    select(IpsEntityMetric).where(
                        IpsEntityMetric.ips_entity_id == entity.id
                    )
                )
            ).all()
        }
        history_metrics = {
            (entity.dimension, entity.value_key, metric.bucket_start): metric.events
            for entity in history_entities.values()
            for metric in (
                await db_session.scalars(
                    select(IpsEntityMetric).where(
                        IpsEntityMetric.ips_entity_id == entity.id
                    )
                )
            ).all()
        }
        assert live_metrics == history_metrics
        assert len(live_metrics) == 3 * 12


class TestNovelty:
    """A value first seen in the history is established; a live one is not."""

    @staticmethod
    def _recording_factory(verdicts: list):  # type: ignore[no-untyped-def]
        """An engine factory that keeps the verdicts, so a test reads the reason."""
        from app.ips.behavior import IPS_DETECTOR_FUNCTIONS
        from app.ips.engine import IpsAnomalyEngine

        def wrap(function):  # type: ignore[no-untyped-def]
            def inner(context):  # type: ignore[no-untyped-def]
                verdict = function(context)
                verdicts.append(verdict)
                return verdict

            return inner

        detectors = {key: wrap(fn) for key, fn in IPS_DETECTOR_FUNCTIONS.items()}

        def factory(session, **kwargs):  # type: ignore[no-untyped-def]
            return IpsAnomalyEngine(session, detectors=detectors, **kwargs)

        return factory

    @staticmethod
    async def _world(db_session, *, anchor: datetime, first_seen: datetime):  # type: ignore[no-untyped-def]
        """A source with a history, a scope row and one judged live bucket.

        The buckets before the hand-off are HISTORY rows, the ones from the
        hand-off on are LIVE rows, as the collector would have written them.
        """
        instance, source, _profile = await ips_source(db_session)
        for hours in range(-24, 3):
            at = first_seen + timedelta(hours=hours)
            await ips_bucket(
                db_session,
                source.id,
                at,
                origin="LIVE" if at >= anchor else "HISTORY",
            )
        entity = IpsEntity(
            instance_id=instance.id,
            log_source_id=source.id,
            dimension=IpsDimension.DESTINATION_PORT,
            value_key="443/tcp",
            display_name="443/tcp",
            first_seen_at=first_seen,
            last_seen_at=first_seen,
            active=True,
            normalizer_version=1,
        )
        db_session.add(entity)
        await db_session.flush()
        for at, events in (
            (first_seen, 10),
            (first_seen + timedelta(hours=1), 15),
            (first_seen + timedelta(hours=2), 30),
        ):
            db_session.add(
                IpsEntityMetric(
                    ips_entity_id=entity.id,
                    bucket_start=at,
                    events=events,
                    bucket_seconds=3600,
                )
            )
        await db_session.flush()
        scope = HistoryBackfillScope(
            instance_id=instance.id,
            family="IPS",
            scope_key="412",
            state="COMPLETE",
            days_requested=7,
            days_target=7,
            target_start=anchor - timedelta(days=7),
            floor=anchor - timedelta(days=7),
            through=anchor - timedelta(hours=1),
            anchor=anchor,
            started_at=anchor - timedelta(days=7),
            finished_at=anchor,
            reason="read the window",
            progress={},
        )
        db_session.add(scope)
        db_session.add(
            CollectionWatermark(
                instance_id=instance.id,
                collector=watermark_collector(412),
                watermark_at=first_seen + timedelta(hours=2),
            )
        )
        db_session.add(
            CollectionWatermark(
                instance_id=instance.id,
                collector=evaluation_collector(412),
                watermark_at=first_seen + timedelta(hours=1),
            )
        )
        await db_session.flush()
        return instance, source, entity

    async def test_a_port_first_seen_in_the_history_is_established(
        self, db_session
    ) -> None:
        anchor = datetime(2026, 8, 3, 10, tzinfo=UTC)
        first_seen = anchor - timedelta(hours=1)
        instance, _source, entity = await self._world(
            db_session, anchor=anchor, first_seen=first_seen
        )

        verdicts: list = []
        report = await IpsInstancePipeline(
            db_session,
            settings=ips_settings(anomaly_open_after_intervals=1),
            clock=lambda: first_seen + timedelta(hours=3),
            engine_factory=self._recording_factory(verdicts),
        ).run(instance)

        assert report.sources[0].opened == 0
        anomalies = (
            await db_session.scalars(
                select(IpsAnomaly).where(IpsAnomaly.ips_entity_id == entity.id)
            )
        ).all()
        assert anomalies == []
        state = (
            await db_session.scalars(
                select(IpsDetectorState).where(
                    IpsDetectorState.ips_entity_id == entity.id,
                    IpsDetectorState.detector_type == "IPS_NEW_DESTINATION_PORT",
                )
            )
        ).one()
        assert state.state == AnomalyState.NORMAL
        # The reason names the hand-off and keeps the real first-seen time.
        new_port = [
            verdict
            for verdict in verdicts
            if verdict.detector_type.value == "IPS_NEW_DESTINATION_PORT"
        ]
        assert new_port
        reason = new_port[0].evidence.reason
        assert first_seen.isoformat() in reason
        assert anchor.isoformat() in reason
        assert "history hand-off" in reason

    async def test_a_port_first_seen_after_the_hand_off_still_opens(
        self, db_session
    ) -> None:
        anchor = datetime(2026, 8, 3, 10, tzinfo=UTC)
        first_seen = anchor + timedelta(hours=1)
        instance, _source, entity = await self._world(
            db_session, anchor=anchor, first_seen=first_seen
        )

        report = await IpsInstancePipeline(
            db_session,
            settings=ips_settings(
                anomaly_open_after_intervals=1, ips_new_port_history_days=1
            ),
            clock=lambda: first_seen + timedelta(hours=3),
        ).run(instance)

        assert report.sources[0].opened == 1
        anomaly = (
            await db_session.scalars(
                select(IpsAnomaly).where(IpsAnomaly.ips_entity_id == entity.id)
            )
        ).one()
        assert anomaly.detector_type == "IPS_NEW_DESTINATION_PORT"
        assert anomaly.state == AnomalyState.OPEN

    async def test_a_history_bucket_below_the_coverage_floor_reads_unknown(
        self, db_session
    ) -> None:
        """A property that did not parse in old events leaves the bucket unknown."""
        anchor = datetime(2026, 8, 3, 10, tzinfo=UTC)
        first_seen = anchor + timedelta(hours=1)
        instance, _source, entity = await self._world(
            db_session, anchor=anchor, first_seen=first_seen
        )
        # One history bucket of the novelty window is below the coverage floor.
        low = anchor - timedelta(hours=2)
        await db_session.execute(
            update(IpsSourceBucket)
            .where(IpsSourceBucket.bucket_start == low)
            .values(destination_port_coverage_ratio=0.5)
        )
        await db_session.commit()

        report = await IpsInstancePipeline(
            db_session,
            settings=ips_settings(
                anomaly_open_after_intervals=1, ips_new_port_history_days=1
            ),
            clock=lambda: first_seen + timedelta(hours=3),
        ).run(instance)

        # The port cannot be called new: the history does not vouch for it.
        assert report.sources[0].opened == 0
        state = (
            await db_session.scalars(
                select(IpsDetectorState).where(
                    IpsDetectorState.ips_entity_id == entity.id,
                    IpsDetectorState.detector_type == "IPS_NEW_DESTINATION_PORT",
                )
            )
        ).one()
        assert state.state == AnomalyState.INSUFFICIENT_DATA


class TestOperatorPath:
    """Every exit of the operator's path: the dispatch through the collector."""

    async def _two_source_fault_rig(self, db_session):  # type: ignore[no-untyped-def]
        """One READING source plus one source already on the ordinary live path."""
        instance = await make_instance(db_session)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        _, live_source = await add_source(db_session, instance, SOURCE_ID + 1)
        await collect(db_session, instance, provider, hcfg(), NOW - BUCKET)
        _, history_source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        await collect(db_session, instance, provider, hcfg(), NOW)
        reading = await scope_of(db_session, instance, str(SOURCE_ID))
        assert reading is not None and reading.state == "READING"
        return instance, provider, history_source, live_source, reading

    @staticmethod
    async def _source_buckets(db_session, source, origin: str):  # type: ignore[no-untyped-def]
        return (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == origin,
                )
            )
        ).all()

    async def test_a_no_answer_creates_no_scope(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        db_session.add(
            HistoryBackfillDecision(
                instance_id=instance.id,
                answer="NO",
                days=None,
                decided_at=NOW,
                decided_by="operator",
                source="UI",
            )
        )
        await db_session.flush()
        provider = ScriptedIpsProvider({}, now=NOW)

        report = await collect(db_session, instance, provider, hcfg(), NOW)

        assert report.history_sources == 0
        assert await scope_of(db_session, instance, str(SOURCE_ID)) is None

    async def test_a_budget_of_zero_reads_nothing(self, db_session) -> None:
        """A search budget too small for one chunk reads nothing at all."""
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        # One search is not enough for a chunk: a chunk costs one search per
        # planned dimension.
        settings = hcfg(history_backfill_searches_per_tick=1)

        report = await collect(db_session, instance, provider, settings, NOW)

        assert report.history_searches == 0
        assert not provider.ips_searches
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "READING"
        assert scope.floor is None

    async def test_the_time_budget_stops_the_step(self, db_session, monkeypatch) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        clock = _FakeClock()
        provider = _ClockAdvancingProvider(
            window_events(target - MARGIN, boundary), now=NOW, clock=clock
        )
        # The first tick proves the floor.
        await collect(db_session, instance, provider, hcfg(), NOW)
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None and scope.floor == target
        # The second tick reads with a clock that has spent the time budget.
        monkeypatch.setattr(ips_collector_module, "time", clock)

        report = await collect(db_session, instance, provider, hcfg(), NOW + BUCKET)

        # The step stops at the time budget: one search, then nothing.
        assert report.history_searches == 1
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "READING"
        assert scope.through is None
        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(IpsSourceBucket.origin == "HISTORY")
            )
        ).all()
        assert rows == []

    async def test_a_time_limit_ends_the_reading_partial(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await collect(db_session, instance, provider, hcfg(), NOW)
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None and scope.state == "READING"
        # The reading started long ago: the next tick ends it.
        decoded = decode_progress(scope.progress)
        scope.progress = encode_progress(
            replace(decoded.progress, started_at=NOW - timedelta(days=30)),
            adapter=decoded.adapter,
        )
        await db_session.commit()

        await collect(db_session, instance, provider, hcfg(), NOW + BUCKET)

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "PARTIAL"
        assert "did not finish" in (scope.reason or "")

    async def test_a_locked_source_is_skipped(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        provider = ScriptedIpsProvider({}, now=NOW)
        # Another worker holds the source's pipeline lock — on a connection
        # that stays checked out for the whole tick, as a real worker's does.
        key = collector_lock_key(instance.id, ips_pipeline_lock_key(SOURCE_ID))
        namespace = hcfg().collection_advisory_lock_namespace
        async with db_session.bind.connect() as holder:
            held = await holder.scalar(
                text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                {"namespace": namespace, "key": key},
            )
            assert held

            report = await collect(db_session, instance, provider, hcfg(), NOW)

            assert report.sources_locked == 1
            assert report.history_sources == 0
            assert await scope_of(db_session, instance, str(SOURCE_ID)) is None
            await holder.execute(
                text("SELECT pg_advisory_unlock(:namespace, :key)"),
                {"namespace": namespace, "key": key},
            )

    async def test_a_provider_failure_mid_step_writes_nothing(self, db_session) -> None:
        instance = await make_instance(db_session)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        _, live_source = await add_source(db_session, instance, SOURCE_ID + 1)
        await collect(db_session, instance, provider, hcfg(), NOW - BUCKET)
        live_before = len(
            (
                await db_session.scalars(
                    select(IpsSourceBucket).where(
                        IpsSourceBucket.log_source_id == live_source.id,
                        IpsSourceBucket.origin == "LIVE",
                    )
                )
            ).all()
        )
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        provider.ips_fault = lambda search: (
            ProviderError("SELECT sensitive_property FROM events")
            if search.window_end < boundary
            else None
        )

        await collect(db_session, instance, provider, hcfg(), NOW)

        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "READING"
        assert scope.last_error_class == "ProviderError"
        assert scope.last_error_at == NOW
        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(IpsSourceBucket.log_source_id == source.id)
            )
        ).all()
        assert rows == []
        live_after = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == live_source.id,
                    IpsSourceBucket.origin == "LIVE",
                )
            )
        ).all()
        assert len(live_after) > live_before

        provider.ips_fault = None
        await collect(db_session, instance, provider, hcfg(), NOW)
        cleared = await scope_of(db_session, instance, str(SOURCE_ID))
        assert cleared is not None
        assert cleared.last_error_class is None and cleared.last_error_at is None

    async def test_capacity_refusal_yields_without_halving_or_marking(self, db_session) -> None:
        instance = await make_instance(db_session)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(window_events(target - MARGIN, boundary), now=NOW)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        provider.ips_fault = lambda search: (
            ProviderError("first fault") if search.window_end < boundary else None
        )
        await collect(db_session, instance, provider, hcfg(), NOW)
        before = await scope_of(db_session, instance, str(SOURCE_ID))
        assert before is not None and before.state == "READING"
        before_progress = copy.deepcopy(before.progress)
        before_through = before.through
        before_error_at = before.last_error_at

        def refuse_history(search):  # type: ignore[no-untyped-def]
            if search.window_end < boundary:
                assert _ARIEL_PRIORITY.get() == "background"
                return ArielPermitUnavailable(7, 2, 4)
            return None

        provider.ips_fault = refuse_history
        await collect(db_session, instance, provider, hcfg(), NOW + BUCKET)

        yielded = await scope_of(db_session, instance, str(SOURCE_ID))
        assert yielded is not None and yielded.state == "READING"
        assert yielded.progress == before_progress and yielded.through == before_through
        assert yielded.last_error_class == "ProviderError"
        assert yielded.last_error_at == before_error_at
        assert yielded.reason == "deferred: no Ariel permit within 7 s"
        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(IpsSourceBucket.log_source_id == source.id)
            )
        ).all()
        assert rows == []
        assert _ARIEL_PRIORITY.get() == "live"

    async def test_a_lifecycle_fault_keeps_the_live_source_and_marks_the_reading_scope(
        self, db_session, monkeypatch
    ) -> None:
        instance, provider, history_source, live_source, before = (
            await self._two_source_fault_rig(db_session)
        )
        before_progress = dict(before.progress)
        before_through = before.through
        history_before = len(
            await self._source_buckets(db_session, history_source, "HISTORY")
        )
        live_before = len(await self._source_buckets(db_session, live_source, "LIVE"))
        watermark_before = await watermark_of(
            db_session, instance, watermark_collector(live_source.qradar_id)
        )
        assert watermark_before is not None
        old_live_watermark = watermark_before.watermark_at
        original = ips_collector_module.scope_action

        def broken_lifecycle(*args, **kwargs):  # type: ignore[no-untyped-def]
            row = kwargs.get("row")
            if row is not None and row.state.value == "READING":
                raise RuntimeError("SELECT sensitive_property FROM events")
            return original(*args, **kwargs)

        monkeypatch.setattr(ips_collector_module, "scope_action", broken_lifecycle)
        failed_at = NOW + BUCKET
        await collect(db_session, instance, provider, hcfg(), failed_at)

        failed = await scope_of(db_session, instance, str(SOURCE_ID))
        assert failed is not None and failed.state == "READING"
        assert failed.progress == before_progress and failed.through == before_through
        assert failed.last_error_class == "RuntimeError"
        assert failed.last_error_at == failed_at
        assert len(
            await self._source_buckets(db_session, history_source, "HISTORY")
        ) == history_before
        assert len(await self._source_buckets(db_session, live_source, "LIVE")) > live_before
        live_watermark = await watermark_of(
            db_session, instance, watermark_collector(live_source.qradar_id)
        )
        assert live_watermark is not None
        assert live_watermark.watermark_at > old_live_watermark  # type: ignore[operator]

        monkeypatch.setattr(ips_collector_module, "scope_action", original)
        await collect(db_session, instance, provider, hcfg(), NOW + 2 * BUCKET)
        cleared = await scope_of(db_session, instance, str(SOURCE_ID))
        assert cleared is not None
        assert cleared.last_error_class is None and cleared.last_error_at is None

    async def test_a_history_run_fault_keeps_the_live_source_and_rolls_the_run_back(
        self, db_session, monkeypatch
    ) -> None:
        instance, provider, history_source, live_source, before = (
            await self._two_source_fault_rig(db_session)
        )
        before_progress = dict(before.progress)
        before_through = before.through
        history_before = len(
            await self._source_buckets(db_session, history_source, "HISTORY")
        )
        live_before = len(await self._source_buckets(db_session, live_source, "LIVE"))
        watermark_before = await watermark_of(
            db_session, instance, watermark_collector(live_source.qradar_id)
        )
        assert watermark_before is not None
        old_live_watermark = watermark_before.watermark_at
        original = IpsCollector._persist_history

        async def persist_then_crash(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await original(self, *args, **kwargs)
            raise RuntimeError("SELECT sensitive_property FROM events")

        monkeypatch.setattr(IpsCollector, "_persist_history", persist_then_crash)
        failed_at = NOW + BUCKET
        await collect(db_session, instance, provider, hcfg(), failed_at)

        failed = await scope_of(db_session, instance, str(SOURCE_ID))
        assert failed is not None and failed.state == "READING"
        assert failed.progress == before_progress and failed.through == before_through
        assert failed.last_error_class == "RuntimeError"
        assert failed.last_error_at == failed_at
        assert len(
            await self._source_buckets(db_session, history_source, "HISTORY")
        ) == history_before
        assert len(await self._source_buckets(db_session, live_source, "LIVE")) > live_before
        live_watermark = await watermark_of(
            db_session, instance, watermark_collector(live_source.qradar_id)
        )
        assert live_watermark is not None
        assert live_watermark.watermark_at > old_live_watermark  # type: ignore[operator]

        monkeypatch.setattr(IpsCollector, "_persist_history", original)
        await collect(db_session, instance, provider, hcfg(), NOW + 2 * BUCKET)
        cleared = await scope_of(db_session, instance, str(SOURCE_ID))
        assert cleared is not None
        assert cleared.last_error_class is None and cleared.last_error_at is None

    async def test_two_sources_read_independently(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_source(db_session, instance, 500)
        await add_source(db_session, instance, 501)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        # The first source's searches fail; the second one reads on.
        provider.ips_fault = lambda search: ProviderError("the appliance refused") if (
            500 in search.qradar_log_source_ids
        ) else None

        await collect(db_session, instance, provider, hcfg(), NOW)

        first = await scope_of(db_session, instance, "500")
        second = await scope_of(db_session, instance, "501")
        assert first is not None and second is not None
        assert first.floor is None
        assert second.floor == target

    async def test_a_worker_restart_mid_step_re_reads_the_same_span(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        source_id = source.id
        instance_id = instance.id
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        provider = ScriptedIpsProvider(
            window_events(target - MARGIN, boundary), now=NOW
        )
        await db_session.commit()
        # The scan tick is rolled back: the worker died before the commit.
        await IpsCollector(
            db_session, provider, settings=hcfg(), clock=lambda: NOW
        ).collect(instance)
        await db_session.rollback()
        # The rolled-back objects are expired: read the console again.
        instance = await db_session.get(QRadarInstance, instance_id)

        assert await scope_of(db_session, instance, str(SOURCE_ID)) is None
        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(IpsSourceBucket.log_source_id == source_id)
            )
        ).all()
        assert rows == []

        # The next worker starts the reading again, from the same window.
        await read_history(db_session, instance, provider, hcfg())
        scope = await scope_of(db_session, instance, str(SOURCE_ID))
        assert scope is not None
        assert scope.state == "COMPLETE"
        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source_id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert len(rows) == 2016


class _FakeClock:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        self.value = 1000.0

    def monotonic(self) -> float:
        return self.value


class _ClockAdvancingProvider(ScriptedIpsProvider):
    """A provider that spends an hour of the history budget on every search."""

    def __init__(self, events, *, now, clock) -> None:  # type: ignore[no-untyped-def]
        super().__init__(events, now=now)
        self.clock = clock

    async def get_ips_keyed(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await super().get_ips_keyed(*args, **kwargs)
        self.clock.value += 3600.0
        return result


    async def test_a_flood_in_the_history_blocks_the_novelty(self, db_session) -> None:
        """A bucket that admitted a flood of ports cannot isolate a new one."""
        anchor = datetime(2026, 8, 3, 10, tzinfo=UTC)
        first_seen = anchor + timedelta(hours=1)
        instance, _source, entity = await self._world(
            db_session, anchor=anchor, first_seen=first_seen
        )
        # The appearance bucket's admission was truncated: a flood of new ports.
        await db_session.execute(
            update(IpsSourceBucket)
            .where(IpsSourceBucket.bucket_start == first_seen)
            .values(
                admission_truncated={
                    "DESTINATION_PORT": True,
                    "DESTINATION_IP": False,
                    "SIGNATURE": False,
                }
            )
        )
        await db_session.commit()

        report = await IpsInstancePipeline(
            db_session,
            settings=ips_settings(
                anomaly_open_after_intervals=1, ips_new_port_history_days=1
            ),
            clock=lambda: first_seen + timedelta(hours=3),
        ).run(instance)

        assert report.sources[0].opened == 0
        state = (
            await db_session.scalars(
                select(IpsDetectorState).where(
                    IpsDetectorState.ips_entity_id == entity.id,
                    IpsDetectorState.detector_type == "IPS_NEW_DESTINATION_PORT",
                )
            )
        ).one()
        assert state.state == AnomalyState.INSUFFICIENT_DATA


class TestCaps:
    """The admission caps of the live path hold for the history path."""

    async def test_the_quota_is_recorded_exactly_as_live(self, db_session) -> None:
        instance = await make_instance(db_session)
        _, source = await add_source(db_session, instance, SOURCE_ID)
        await record_yes(db_session, instance)
        boundary = boundary_of(NOW)
        target = boundary - timedelta(days=WINDOW_DAYS)
        # More new ports in one bucket than the per-bucket quota allows.
        events = {}
        for at in buckets(target, boundary):
            events[at] = [
                {
                    "sip": "192.0.2.10",
                    "dip": "198.51.100.20",
                    "dport": 1000 + index,
                    "proto": 6,
                    "qid": 5_000_123,
                    "qname": "IPS signature 5000123",
                    "sig": "SIG-1",
                    "act": "deny",
                    "events": 5,
                    "records": 5,
                }
                for index in range(30)
            ]
        provider = ScriptedIpsProvider(events, now=NOW)
        settings = hcfg(ips_max_new_entities_per_bucket=20)

        await read_history(db_session, instance, provider, settings)

        rows = (
            await db_session.scalars(
                select(IpsSourceBucket).where(
                    IpsSourceBucket.log_source_id == source.id,
                    IpsSourceBucket.origin == "HISTORY",
                )
            )
        ).all()
        assert rows
        first = min(rows, key=lambda row: row.bucket_start)
        # Twenty ports are admitted, the rest are rejected over the quota.
        assert first.admitted["DESTINATION_PORT"] == 20
        assert first.rejected_over_quota["DESTINATION_PORT"] == 10
        assert first.admission_truncated["DESTINATION_PORT"] is True
        assert first.below_admission_threshold["DESTINATION_PORT"] == 0
        entities = (
            await db_session.scalars(
                select(IpsEntity).where(IpsEntity.log_source_id == source.id)
            )
        ).all()
        # Twenty ports in the first bucket, the remaining ten in the second, plus
        # the destination IP and the signature.
        assert len(entities) == 32


class TestGuards:
    """The learning-only guards: a history row never reaches a verdict."""

    """The learning-only guards: a history row never reaches a verdict."""

    async def test_a_history_bucket_is_never_pending(self, db_session) -> None:
        """The pending read takes no history bucket, whatever the watermarks say."""
        instance, source, _profile = await ips_source(db_session)
        at = datetime(2026, 8, 3, 10, tzinfo=UTC)
        await ips_bucket(db_session, source.id, at, origin="HISTORY")
        collection = watermark_collector(412)
        evaluation = evaluation_collector(412)

        async def judged() -> list[datetime]:
            report = await IpsInstancePipeline(
                db_session,
                settings=ips_settings(),
                clock=lambda: at + timedelta(hours=1),
            ).run(instance)
            return report.sources[0].evaluated_buckets

        # The evaluation watermark is unset: the pending read takes every bucket
        # up to the collection watermark, and the history bucket is not one.
        db_session.add(
            CollectionWatermark(
                instance_id=instance.id, collector=collection, watermark_at=at
            )
        )
        await db_session.flush()
        assert await judged() == []

        # The evaluation watermark is set wrongly: the history bucket is before it.
        # The pipeline creates the evaluation watermark when it is missing, so the
        # row is already there: it is moved, never inserted a second time.
        evaluation_watermark = await watermark_of(db_session, instance, evaluation)
        assert evaluation_watermark is not None
        evaluation_watermark.watermark_at = at + timedelta(hours=1)
        await db_session.commit()
        assert await judged() == []

        # The collection watermark is unset: nothing is judged at all.
        collection_watermark = await watermark_of(db_session, instance, collection)
        assert collection_watermark is not None
        collection_watermark.watermark_at = None
        await db_session.commit()
        assert await judged() == []

    async def test_a_history_bucket_never_reaches_an_f1_breach(
        self, db_session
    ) -> None:
        _instance, source, _profile = await ips_source(db_session)
        at = datetime(2026, 8, 3, 10, tzinfo=UTC)
        await ips_bucket(db_session, source.id, at, origin="HISTORY", total_events=900)
        rule = CustomRule(
            name="ips-volume",
            subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
            log_source_id=source.id,
            metric=CustomRuleMetric.TOTAL_EVENTS.value,
            operator="GT",
            threshold_value=100.0,
            sustain_buckets=1,
            recovery_buckets=1,
            severity=Severity.HIGH,
            enabled=True,
            consecutive_breaching=0,
            consecutive_under=0,
        )
        db_session.add(rule)
        await db_session.flush()
        await arm_f1(db_session, CustomRuleSubjectKind.IPS_SOURCE, at - timedelta(hours=1))

        report = await CustomRuleEngine(
            db_session, f1_settings(), clock=lambda: at + timedelta(hours=1)
        ).run()

        assert report.opened == 0
        assert (await db_session.scalars(select(CustomRuleBreach))).all() == []

    async def test_a_history_entity_never_reaches_an_f1_breach(self, db_session) -> None:
        _instance, source, _profile = await ips_source(db_session)
        at = datetime(2026, 8, 3, 10, tzinfo=UTC)
        entity = IpsEntity(
            instance_id=source.instance_id,
            log_source_id=source.id,
            dimension=IpsDimension.DESTINATION_PORT,
            value_key="443/tcp",
            display_name="443/tcp",
            first_seen_at=at - timedelta(hours=1),
            last_seen_at=at,
            active=True,
            normalizer_version=1,
        )
        db_session.add(entity)
        await db_session.flush()
        await ips_bucket(db_session, source.id, at, origin="HISTORY")
        db_session.add(
            IpsEntityMetric(
                ips_entity_id=entity.id, bucket_start=at, events=900, bucket_seconds=3600
            )
        )
        rule = CustomRule(
            name="ips-entity",
            subject_kind=CustomRuleSubjectKind.IPS_ENTITY,
            ips_entity_id=entity.id,
            metric=CustomRuleMetric.EVENTS.value,
            operator="GT",
            threshold_value=100.0,
            sustain_buckets=1,
            recovery_buckets=1,
            severity=Severity.HIGH,
            enabled=True,
            consecutive_breaching=0,
            consecutive_under=0,
        )
        db_session.add(rule)
        await db_session.flush()
        await arm_f1(db_session, CustomRuleSubjectKind.IPS_ENTITY, at - timedelta(hours=1))

        report = await CustomRuleEngine(
            db_session, f1_settings(), clock=lambda: at + timedelta(hours=1)
        ).run()

        assert report.opened == 0
        assert (await db_session.scalars(select(CustomRuleBreach))).all() == []

    async def test_the_preview_reads_a_history_bucket(self, db_session) -> None:
        _instance, source, _profile = await ips_source(db_session)
        at = datetime(2026, 8, 3, 10, tzinfo=UTC)
        await ips_bucket(db_session, source.id, at, origin="HISTORY", total_events=900)

        result = await replay(
            db_session,
            metric=CustomRuleMetric.TOTAL_EVENTS,
            operator="GT",
            threshold_value=100.0,
            sustain_buckets=1,
            recovery_buckets=1,
            severity="HIGH",
            subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
            log_source_id=source.id,
            source_class=None,
            waf_endpoint_id=None,
            ips_entity_id=None,
            pw_value_id=None,
            pw_coverage_floor=None,
            hours=2,
            interval_seconds=3600,
            now=at + timedelta(hours=1),
        )

        measured = [b for b in result.buckets if b.measurement != "NOT_MEASURED"]
        assert [b.value for b in measured] == [900.0]
        assert result.fired_count == 1
