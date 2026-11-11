"""SOC-16f5: a lane proves absence only after its mention backfill is COMPLETE.

History buckets count for WAF and IPS. A WAF window of volume-only history is
not collected. A failed mention write marks that source PENDING.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.history.planner import ScopeProgress
from app.history.waf import HistoryVolumeBucket
from app.models.entity import EntityMention, EntityMentionBackfill
from app.models.enums import BucketCompleteness, WafTelemetryStatus
from app.models.waf import WafEndpoint, WafEndpointMetric
from app.schemas.entity_timeline import EntityTimelineKind, EntityTimelineLane
from app.schemas.related_activity import RelatedSectionState
from app.security.rbac import Principal
from app.services.entity_mentions import MENTION_WRITE_FAILED, mark_mentions_incomplete
from app.services.entity_timeline import (
    REASON_BOUNDED,
    REASON_INDEX_INCOMPLETE,
    REASON_NO_COVERAGE,
    EntityTimelineService,
)
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_entity_mention_writers import waf_collect, waf_provider
from tests.integration.test_entity_timeline import (
    END,
    IP,
    START,
    T0,
    _ips_bucket,
    _mention,
    _waf_bucket,
    _watermark,
)
from tests.integration.test_soc16d1_entity_lanes import _catchall_bucket
from tests.integration.test_waf_breakdown_collection import OUTCOMES_ON
from tests.integration.test_waf_keyed_collection import ScriptedWaf, make_source
from tests.integration.test_waf_productionization import STEP, cfg
from tests.integration.test_waf_productionization import T0 as WAF_T0

pytestmark = pytest.mark.integration

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
OTHER = "198.51.100.9"


def _settings(**overrides: object):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(
        update={
            "waf_collection_enabled": True,
            "ips_collection_enabled": True,
            "catchall_collection_enabled": True,
            "pw_collection_enabled": True,
            **overrides,
        }
    )


async def _timeline(session, instance_id, *, value=IP, value_norm=IP, start=START, end=END):  # type: ignore[no-untyped-def]
    return await EntityTimelineService(
        session, _settings(), READER, now=datetime.now(UTC)
    ).read(
        instance_id=instance_id,
        kind=EntityTimelineKind.IP,
        value=value,
        value_norm=value_norm,
        start=start,
        end=end,
        limit=50,
        cursor=None,
    )


def _lane(page, source: EntityTimelineLane):  # type: ignore[no-untyped-def]
    found = [lane for lane in page.lanes if lane.source is source]
    assert len(found) == 1
    return found[0]


def _complete(instance_id: uuid.UUID, *sources: str) -> list[EntityMentionBackfill]:
    return [
        EntityMentionBackfill(
            instance_id=instance_id,
            source=source,
            status="COMPLETE",
            rows_written=0,
            completed_at=T0,
        )
        for source in sources
    ]


@contextmanager
def _statements(session):  # type: ignore[no-untyped-def]
    found: list[str] = []

    def record(*args):  # type: ignore[no-untyped-def]
        found.append(args[2])

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield found
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _backfill(session, instance_id, source: str) -> EntityMentionBackfill | None:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(EntityMentionBackfill).where(
            EntityMentionBackfill.instance_id == instance_id,
            EntityMentionBackfill.source == source,
        )
    )


async def test_mark_mentions_incomplete_resets_each_source_in_one_statement(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    db_session.add(
        EntityMentionBackfill(
            instance_id=instance.id,
            source="WAF_TOP",
            status="COMPLETE",
            cursor={"bucket_start": T0.isoformat()},
            rows_written=4,
            started_at=T0,
            completed_at=T0,
            last_error=None,
        )
    )
    await db_session.flush()

    with _statements(db_session) as quiet:
        await mark_mentions_incomplete(db_session, instance.id, ())
    assert quiet == []

    with _statements(db_session) as wrote:
        await mark_mentions_incomplete(db_session, instance.id, ("WAF_TOP", "IPS_TOP", "WAF_TOP"))
    await db_session.flush()
    inserts = [
        sql
        for sql in wrote
        if "entity_mention_backfill" in sql.lower() and sql.lstrip().lower().startswith("insert")
    ]
    assert len(inserts) == 1
    assert "on conflict" in inserts[0].lower()

    kept = await _backfill(db_session, instance.id, "WAF_TOP")
    added = await _backfill(db_session, instance.id, "IPS_TOP")
    assert kept is not None and added is not None
    assert kept.status == "PENDING"
    assert kept.cursor is None
    assert kept.completed_at is None
    assert kept.last_error == MENTION_WRITE_FAILED
    assert kept.rows_written == 4
    assert kept.started_at == T0
    assert added.status == "PENDING"
    assert added.cursor is None
    assert added.completed_at is None
    assert added.last_error == MENTION_WRITE_FAILED
    assert added.rows_written == 0


async def test_unknown_source_fails_the_check_and_propagates(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    with pytest.raises(DBAPIError):
        await mark_mentions_incomplete(db_session, instance.id, ("NOT_A_SOURCE",))
    await db_session.rollback()


async def test_live_window_without_a_row_cannot_prove_absence(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=1605, name="waf", identifier="other"
    )
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/login",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=T0,
        last_seen_at=T0,
        is_active=True,
        tracked_since=T0,
    )
    db_session.add(endpoint)
    await db_session.flush()
    db_session.add_all(
        [
            _waf_bucket(source.id),
            _catchall_bucket(source.id, T0),
            _watermark(instance.id),
            WafEndpointMetric(
                waf_endpoint_id=endpoint.id,
                bucket_start=T0,
                bucket_seconds=300,
                request_count=10,
                client_sources=1,
                top_clients=[{"source_ip": IP, "requests": 10}],
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                query_provenance={},
                normalizer_version=1,
            ),
            _mention(
                instance.id,
                source="WAF_TOP",
                ref_table="waf_endpoint_metric",
                ref_id=str(endpoint.id),
                occurred_at=T0,
            ),
        ]
    )
    await db_session.flush()

    present = await _timeline(db_session, instance.id)
    shown = _lane(present, EntityTimelineLane.WAF_TOP)
    assert any(item.source is EntityTimelineLane.WAF_TOP for item in present.items)
    assert shown.state is RelatedSectionState.PARTIAL
    assert shown.reason_code == REASON_INDEX_INCOMPLETE
    assert shown.total == 1

    empty = await _timeline(db_session, instance.id, value=OTHER, value_norm=OTHER)
    absent = _lane(empty, EntityTimelineLane.WAF_TOP)
    assert absent.state is RelatedSectionState.PARTIAL
    assert absent.reason_code == REASON_INDEX_INCOMPLETE
    assert absent.state is not RelatedSectionState.OK
    assert absent.total is None

    offense = _lane(empty, EntityTimelineLane.OFFENSE)
    assert offense.state is RelatedSectionState.PARTIAL
    assert offense.reason_code == REASON_INDEX_INCOMPLETE
    assert offense.total is None

    catchall = _lane(empty, EntityTimelineLane.CATCHALL)
    assert catchall.state is RelatedSectionState.PARTIAL
    assert catchall.reason_code == REASON_INDEX_INCOMPLETE
    assert catchall.total is None


async def test_complete_row_restores_an_empty_ok_where_the_measurement_proves_it(  # type: ignore[no-untyped-def]
    db_session,
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=1606, name="exact", identifier="other"
    )
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/login",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=T0,
        last_seen_at=T0,
        is_active=True,
        tracked_since=T0,
    )
    db_session.add(endpoint)
    await db_session.flush()
    db_session.add_all(
        [
            _waf_bucket(source.id),
            _catchall_bucket(source.id, T0),
            _watermark(instance.id),
            *_complete(instance.id, "WAF_TOP", "OFFENSE", "CATCHALL"),
            WafEndpointMetric(
                waf_endpoint_id=endpoint.id,
                bucket_start=T0,
                bucket_seconds=300,
                request_count=10,
                client_sources=1,
                top_clients=[{"source_ip": IP, "requests": 10}],
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                query_provenance={},
                normalizer_version=1,
            ),
            _mention(
                instance.id,
                source="WAF_TOP",
                ref_table="waf_endpoint_metric",
                ref_id=str(endpoint.id),
                occurred_at=T0,
            ),
        ]
    )
    await db_session.flush()

    present = await _timeline(db_session, instance.id)
    shown = _lane(present, EntityTimelineLane.WAF_TOP)
    assert shown.state is RelatedSectionState.OK
    assert shown.total == 1
    assert any(item.source is EntityTimelineLane.WAF_TOP for item in present.items)

    # No metric rows in this window: the exact bucket has a complete client list
    # of length zero, so the other address is a measured absence.
    later = T0 + timedelta(hours=2)
    bare = _waf_bucket(source.id)
    bare.bucket_start = later
    db_session.add(bare)
    await db_session.flush()
    empty = await _timeline(
        db_session,
        instance.id,
        value=OTHER,
        value_norm=OTHER,
        start=later - timedelta(minutes=1),
        end=later + timedelta(minutes=10),
    )
    absent = _lane(empty, EntityTimelineLane.WAF_TOP)
    assert absent.state is RelatedSectionState.OK
    assert absent.total == 0

    offense = _lane(present, EntityTimelineLane.OFFENSE)
    assert offense.state is RelatedSectionState.OK
    assert offense.total == 0
    catchall = _lane(present, EntityTimelineLane.CATCHALL)
    assert catchall.state is RelatedSectionState.OK
    assert catchall.total == 0


async def test_history_exact_can_be_ok_and_volume_only_is_not_collected(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=1607, name="history", identifier="other"
    )
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host=None,
        normalized_endpoint="/login",
        http_method="GET",
        normalizer_version=1,
        first_seen_at=T0,
        last_seen_at=T0,
        is_active=True,
        tracked_since=T0,
    )
    db_session.add(endpoint)
    await db_session.flush()
    exact = _waf_bucket(source.id)
    exact.origin = "HISTORY"
    volume_at = T0 + timedelta(hours=7)
    volume = _waf_bucket(source.id)
    volume.origin = "HISTORY"
    volume.bucket_start = volume_at
    volume.client_measurement = WafTelemetryStatus.NOT_COLLECTED
    ips = _ips_bucket(source.id)
    ips.origin = "HISTORY"
    db_session.add_all(
        [
            exact,
            volume,
            ips,
            *_complete(instance.id, "WAF_TOP", "IPS_TOP"),
            WafEndpointMetric(
                waf_endpoint_id=endpoint.id,
                bucket_start=T0,
                bucket_seconds=300,
                request_count=10,
                client_sources=1,
                top_clients=[{"source_ip": IP, "requests": 10}],
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                query_provenance={},
                normalizer_version=1,
                collection_source="ariel_history",
            ),
            _mention(
                instance.id,
                source="WAF_TOP",
                ref_table="waf_endpoint_metric",
                ref_id=str(endpoint.id),
                occurred_at=T0,
            ),
            _mention(
                instance.id,
                source="IPS_TOP",
                ref_table="ips_source_bucket",
                ref_id=str(source.id),
                occurred_at=T0,
            ),
        ]
    )
    await db_session.flush()

    page = await _timeline(db_session, instance.id)
    waf = _lane(page, EntityTimelineLane.WAF_TOP)
    ips_lane = _lane(page, EntityTimelineLane.IPS_TOP)
    assert waf.state is RelatedSectionState.OK and waf.total == 1
    assert ips_lane.state is RelatedSectionState.OK and ips_lane.total == 1

    volume_page = await _timeline(
        db_session,
        instance.id,
        value=OTHER,
        value_norm=OTHER,
        start=volume_at - timedelta(minutes=1),
        end=volume_at + timedelta(minutes=10),
    )
    volume_lane = _lane(volume_page, EntityTimelineLane.WAF_TOP)
    assert volume_lane.state is RelatedSectionState.NOT_COLLECTED
    assert volume_lane.reason_code == REASON_NO_COVERAGE
    assert volume_lane.state is not RelatedSectionState.PARTIAL
    assert volume_lane.total is None


async def test_ips_with_a_complete_index_still_does_not_prove_absence(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=1608, name="ips", identifier="other"
    )
    bucket = _ips_bucket(source.id)
    bucket.origin = "HISTORY"
    db_session.add_all([bucket, *_complete(instance.id, "IPS_TOP", "IPS_ENTITY")])
    await db_session.flush()

    page = await _timeline(db_session, instance.id)
    for source_name in (EntityTimelineLane.IPS_TOP, EntityTimelineLane.IPS_ENTITY):
        lane = _lane(page, source_name)
        assert lane.state is RelatedSectionState.PARTIAL
        assert lane.reason_code == REASON_BOUNDED
        assert lane.state is not RelatedSectionState.OK
        assert lane.total is None


async def _failing_record(session, *_args, **_kwargs):  # type: ignore[no-untyped-def]
    await session.execute(text("SELECT 1/0"))


async def test_live_waf_mention_failure_marks_the_source_pending(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.collectors.waf_endpoint_collector.record_mentions", _failing_record
    )
    settings = cfg(**OUTCOMES_ON)
    instance, _source = await waf_collect(db_session, waf_provider(1), settings, reset=False)
    assert len((await db_session.scalars(select(WafEndpointMetric))).all()) == 1
    assert (await db_session.scalars(select(EntityMention))).all() == []
    row = await _backfill(db_session, instance.id, "WAF_TOP")
    assert row is not None
    assert row.status == "PENDING"
    assert row.cursor is None
    assert row.completed_at is None
    assert row.last_error == MENTION_WRITE_FAILED

    page = await _timeline(
        db_session,
        instance.id,
        value="198.51.100.1",
        value_norm="198.51.100.1",
        start=WAF_T0,
        end=WAF_T0 + STEP,
    )
    lane = _lane(page, EntityTimelineLane.WAF_TOP)
    assert lane.state is not RelatedSectionState.OK
    assert lane.reason_code == REASON_INDEX_INCOMPLETE


async def test_history_waf_mention_failure_marks_the_source_pending(  # type: ignore[no-untyped-def]
    db_session, monkeypatch
) -> None:
    settings = cfg(**OUTCOMES_ON, history_backfill_max_lower_bound_share=1.0)
    provider = ScriptedWaf({WAF_T0: [("/portal/login", "POST", 100)]})
    provider.derive_breakdowns()
    _profile, source, instance, factory = await make_source(db_session, provider, settings)
    collector = factory(WAF_T0 + STEP)
    keyed = await collector._observations_keyed(
        source, WAF_T0, WAF_T0 + STEP, int(STEP.total_seconds())
    )
    await collector.persist_history_volume(
        source,
        [HistoryVolumeBucket(batch=keyed.buckets[WAF_T0], collected_at=WAF_T0 + STEP)],
        floor=WAF_T0,
    )
    await db_session.flush()
    monkeypatch.setattr(
        "app.collectors.waf_endpoint_collector.record_mentions", _failing_record
    )
    step = await collector.history_breakdown_step(
        source,
        ScopeProgress(target_start=WAF_T0, started_at=WAF_T0, floor=WAF_T0, scan_cursor=None),
        boundary=WAF_T0 + STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    await db_session.flush()
    assert step.metrics_updated > 0
    assert (await db_session.scalars(select(WafEndpointMetric))).all()
    assert (await db_session.scalars(select(EntityMention))).all() == []
    row = await _backfill(db_session, instance.id, "WAF_TOP")
    assert row is not None
    assert row.status == "PENDING"
    assert row.cursor is None
    assert row.completed_at is None
    assert row.last_error == MENTION_WRITE_FAILED

    page = await _timeline(
        db_session,
        instance.id,
        value="198.51.100.2",
        value_norm="198.51.100.2",
        start=WAF_T0,
        end=WAF_T0 + STEP,
    )
    lane = _lane(page, EntityTimelineLane.WAF_TOP)
    assert lane.state is not RelatedSectionState.OK
    assert lane.reason_code == REASON_INDEX_INCOMPLETE


async def test_a_failed_incomplete_mark_propagates(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def explode(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("backfill mark failed")

    monkeypatch.setattr(
        "app.collectors.waf_endpoint_collector.record_mentions", _failing_record
    )
    monkeypatch.setattr(
        "app.collectors.waf_endpoint_collector.mark_mentions_incomplete", explode
    )
    settings = cfg(**OUTCOMES_ON)
    with pytest.raises(RuntimeError, match="backfill mark failed"):
        await waf_collect(db_session, waf_provider(1), settings, reset=False)
