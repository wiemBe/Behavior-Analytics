"""SOC-16d1 collector-to-timeline lanes, honesty and parent lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select

from app.collectors.catchall_collector import CatchallCollector
from app.collectors.pw_collector import PwCollector
from app.core.config import get_settings
from app.models.catchall import CatchallBucket
from app.models.entity import EntityMention
from app.models.enums import SearchFamily
from app.models.monitoring import CollectionWatermark
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.models.search_cadence import SearchCadenceSetting
from app.schemas.entity_timeline import EntityTimelineKind
from app.security.rbac import Principal
from app.services.catchall_retention import CatchallRetention
from app.services.entity_timeline import EntityTimelineService
from app.services.pw_retention import PwRetention
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_catchall_collection import (
    BUCKET,
    FIRST,
    LAST,
    Script,
    row,
    steady,
)
from tests.integration.test_catchall_collection import NOW as CATCHALL_NOW
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_pw_collection import NOW as PW_NOW
from tests.integration.test_pw_collection import (
    add_watch,
    provider_with,
)
from tests.integration.test_pw_collection import cfg as pw_cfg

pytestmark = pytest.mark.integration

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ANOMALY_READER = Principal(
    subject="anomaly-reader", permissions=frozenset({"read:anomalies"})
)


def _settings(**overrides: Any):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(
        update={
            "catchall_collection_enabled": True,
            "pw_collection_enabled": True,
            **overrides,
        }
    )


async def _timeline(
    session,
    instance_id: uuid.UUID,
    *,
    kind: EntityTimelineKind,
    value: str,
    value_norm: str,
    start: datetime,
    end: datetime,
    principal: Principal = READER,
    settings=None,
):  # type: ignore[no-untyped-def]
    return await EntityTimelineService(
        session,
        settings or _settings(),
        principal,
        now=end,
    ).read(
        instance_id=instance_id,
        kind=kind,
        value=value,
        value_norm=value_norm,
        start=start,
        end=end,
        limit=200,
        cursor=None,
    )


def _catchall_bucket(
    source_id: uuid.UUID,
    at: datetime,
    *,
    origin: str = "LIVE",
    top: list[dict[str, object]] | None = None,
    distinct: int = 0,
    untracked: int = 0,
) -> CatchallBucket:
    return CatchallBucket(
        catchall_log_source_id=source_id,
        bucket_start=at,
        bucket_seconds=300,
        origin=origin,
        events=0,
        records=0,
        distinct_senders=distinct,
        distinct_senders_lower_bound=False,
        measurement="EXACT",
        top_senders=top or [],
        admitted=0,
        below_admission_threshold=0,
        rejected_over_quota=0,
        untracked_senders=untracked,
        untracked_events=0,
        query_provenance={},
        collected_at=at + timedelta(minutes=5),
    )


def _pw_bucket(
    watch: PropertyWatch,
    at: datetime,
    *,
    origin: str = "LIVE",
    admission_truncated: bool = False,
) -> PwBucket:
    return PwBucket(
        watch_id=watch.id,
        bucket_start=at,
        bucket_seconds=300,
        watch_version=watch.watch_version,
        origin=origin,
        total_events=0,
        parsed_events=0,
        unparsed_events=0,
        untracked_events=1 if admission_truncated else 0,
        values_measurement="EXACT",
        pinned_measurement="NOT_REQUESTED",
        totals_complete=True,
        observed_values=1 if admission_truncated else 0,
        tracked_values_observed=0,
        admitted_values=0,
        below_admission_threshold=1 if admission_truncated else 0,
        rejected_over_quota=0,
        admission_truncated=admission_truncated,
        scope_member_count=1,
        scope_fingerprint="scope",
        scope_changed=False,
        in_maintenance=False,
        query_provenance={},
        collected_at=at + timedelta(minutes=5),
    )


async def test_real_catchall_collector_round_trips_both_kinds_and_is_idempotent(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=1001, name="SIM Generic", type_id=67
    )
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.CATCHALL_SENDERS.value,
            cadence_seconds=3600,
            updated_at=CATCHALL_NOW,
        )
    )
    await db_session.flush()
    provider = Script(steady([row("192.0.2.77", 12), row("Edge-Relay.", 11)]))
    collector = CatchallCollector(
        db_session,
        provider,
        settings=_settings(search_cadence_max_searches_per_tick=50),
        clock=lambda: CATCHALL_NOW,
    )

    await collector.collect(instance)
    first_count = await db_session.scalar(
        select(func.count()).select_from(EntityMention).where(
            EntityMention.source == "CATCHALL"
        )
    )
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == "catchall_senders",
        )
    )
    assert watermark is not None
    watermark.watermark_at = None
    await collector.collect(instance)
    second_count = await db_session.scalar(
        select(func.count()).select_from(EntityMention).where(
            EntityMention.source == "CATCHALL"
        )
    )
    assert first_count == second_count == 24

    ip = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.IP,
        value="192.0.2.77",
        value_norm="192.0.2.77",
        start=FIRST,
        end=LAST + timedelta(seconds=BUCKET),
    )
    host = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.HOST,
        value="Edge-Relay.",
        value_norm="edge-relay",
        start=FIRST,
        end=LAST + timedelta(seconds=BUCKET),
    )
    assert len(ip.items) == len(host.items) == 12
    assert {item.href for item in ip.items + host.items} == {"/fleet/catchall"}
    assert {item.role for item in ip.items} == {"SOURCE_IP"}
    assert {item.role for item in host.items} == {"HOST"}
    assert ip.dangling == host.dangling == 0
    assert source.id


async def test_real_property_watch_collector_round_trips_to_the_owning_watch(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    provider = provider_with({"CORP\\Alice": 10})
    await PwCollector(
        db_session, provider, settings=pw_cfg(), clock=lambda: PW_NOW
    ).collect(instance)

    for query in ("alice", "OTHER\\Alice"):
        timeline = await _timeline(
            db_session,
            instance.id,
            kind=EntityTimelineKind.USER,
            value=query,
            value_norm=query.lower(),
            start=PW_NOW - timedelta(minutes=10),
            end=PW_NOW + timedelta(minutes=5),
        )
        items = [item for item in timeline.items if item.source.value == "PW_VALUE"]
        assert len(items) == 1
        assert items[0].href == f"/behavior/watches/{watch.id}"
        assert items[0].role == "USER"
        assert timeline.dangling == 0


async def test_lane_honesty_live_empty_history_bounded_and_forbidden(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=7001, name="catch", type_id=67
    )
    user_watch = await add_watch(db_session, instance)
    at = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
    db_session.add_all([_catchall_bucket(source.id, at), _pw_bucket(user_watch, at)])
    await db_session.flush()

    host = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.HOST,
        value="missing-host",
        value_norm="missing-host",
        start=at,
        end=at + timedelta(minutes=5),
    )
    user = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="missing-user",
        value_norm="missing-user",
        start=at,
        end=at + timedelta(minutes=5),
    )
    by_host = {lane.source.value: lane for lane in host.lanes}
    by_user = {lane.source.value: lane for lane in user.lanes}
    assert by_host["CATCHALL"].state.value == "OK" and by_host["CATCHALL"].total == 0
    assert by_host["PW_VALUE"].state.value == "NOT_COLLECTED"
    assert by_user["PW_VALUE"].state.value == "OK" and by_user["PW_VALUE"].total == 0

    switched_off = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="missing-user",
        value_norm="missing-user",
        start=at,
        end=at + timedelta(minutes=5),
        settings=_settings(pw_collection_enabled=False),
    )
    off_lane = next(lane for lane in switched_off.lanes if lane.source.value == "PW_VALUE")
    assert off_lane.state.value == "NOT_COLLECTED"
    assert off_lane.reason_code == "entity_timeline.reason.family_off"

    (await db_session.get(CatchallBucket, (source.id, at))).origin = "HISTORY"
    (await db_session.get(PwBucket, (user_watch.id, at))).origin = "HISTORY"
    history_host = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.HOST,
        value="missing-host",
        value_norm="missing-host",
        start=at,
        end=at + timedelta(minutes=5),
    )
    history_user = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="missing-user",
        value_norm="missing-user",
        start=at,
        end=at + timedelta(minutes=5),
    )
    assert (
        next(lane for lane in history_host.lanes if lane.source.value == "CATCHALL")
        .state.value
        == "NOT_COLLECTED"
    )
    assert (
        next(lane for lane in history_user.lanes if lane.source.value == "PW_VALUE")
        .state.value
        == "NOT_COLLECTED"
    )

    (await db_session.get(CatchallBucket, (source.id, at))).origin = "LIVE"
    (await db_session.get(CatchallBucket, (source.id, at))).distinct_senders = 2
    (await db_session.get(CatchallBucket, (source.id, at))).untracked_senders = 1
    (await db_session.get(PwBucket, (user_watch.id, at))).origin = "LIVE"
    (await db_session.get(PwBucket, (user_watch.id, at))).admission_truncated = True
    bounded_host = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.HOST,
        value="missing-host",
        value_norm="missing-host",
        start=at,
        end=at + timedelta(minutes=5),
    )
    bounded_user = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="missing-user",
        value_norm="missing-user",
        start=at,
        end=at + timedelta(minutes=5),
    )
    assert (
        next(lane for lane in bounded_host.lanes if lane.source.value == "CATCHALL")
        .state.value
        == "PARTIAL"
    )
    assert (
        next(lane for lane in bounded_user.lanes if lane.source.value == "PW_VALUE")
        .state.value
        == "PARTIAL"
    )

    forbidden = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="missing-user",
        value_norm="missing-user",
        start=at,
        end=at + timedelta(minutes=5),
        principal=ANOMALY_READER,
    )
    forbidden_by_source = {lane.source.value: lane for lane in forbidden.lanes}
    assert forbidden_by_source["CATCHALL"].permission == "read:fleet"
    assert forbidden_by_source["PW_VALUE"].permission == "read:property_watches"


async def test_retention_deletes_mentions_before_both_parent_kinds(db_session) -> None:
    old = datetime(2026, 9, 1, tzinfo=UTC)
    now = datetime(2026, 11, 11, tzinfo=UTC)
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=8001, name="catch", type_id=67
    )
    watch = await add_watch(db_session, instance)
    value = PwValue(
        watch_id=watch.id,
        watch_version=1,
        value_key="alice",
        component_0="alice",
        pinned=False,
        active=False,
        first_seen_at=old,
        last_seen_at=old,
        tracked_since=old,
        tracked_until=old,
        retired_at=old,
    )
    db_session.add_all([_catchall_bucket(source.id, old), value])
    await db_session.flush()
    db_session.add(
        PwValueMetric(
            pw_value_id=value.id,
            bucket_start=old,
            events=3,
            bucket_seconds=300,
        )
    )
    db_session.add_all(
        [
            EntityMention(
                instance_id=instance.id,
                kind="HOST",
                value_norm="old-host",
                role="HOST",
                source="CATCHALL",
                ref_table="catchall_bucket",
                ref_id=str(source.id),
                occurred_at=old,
            ),
            EntityMention(
                instance_id=instance.id,
                kind="USER",
                value_norm="alice",
                role="USER",
                source="PW_VALUE",
                ref_table="pw_value_metric",
                ref_id=str(value.id),
                occurred_at=old,
            ),
        ]
    )
    await db_session.flush()

    await CatchallRetention(db_session, settings=_settings(), clock=lambda: now).run()
    await PwRetention(db_session, settings=_settings(), clock=lambda: now).run()

    assert await db_session.scalar(select(func.count()).select_from(EntityMention)) == 0
    assert await db_session.get(CatchallBucket, (source.id, old)) is None
    assert await db_session.get(PwValueMetric, (value.id, old)) is None


@pytest.mark.parametrize("bucket_count", [1, 50])
async def test_two_new_parent_tables_add_two_fixed_hydrator_statements(
    db_session, bucket_count: int
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, qradar_id=9001 + bucket_count, name="catch", type_id=67
    )
    watch = await add_watch(
        db_session,
        instance,
        properties=[{"kind": "CUSTOM_PROPERTY", "name": "sourceip"}],
    )
    value = PwValue(
        watch_id=watch.id,
        watch_version=1,
        value_key="192.0.2.99",
        component_0="192.0.2.99",
        pinned=False,
        active=True,
        first_seen_at=PW_NOW,
        last_seen_at=PW_NOW,
        tracked_since=PW_NOW,
    )
    db_session.add(value)
    await db_session.flush()
    for index in range(bucket_count):
        at = PW_NOW + timedelta(minutes=5 * index)
        db_session.add(
            _catchall_bucket(
                source.id,
                at,
                top=[{"identifier": "192.0.2.99", "events": 4}],
                distinct=1,
            )
        )
        db_session.add(_pw_bucket(watch, at))
        db_session.add(
            PwValueMetric(
                pw_value_id=value.id,
                bucket_start=at,
                events=4,
                bucket_seconds=300,
            )
        )
        db_session.add_all(
            [
                EntityMention(
                    instance_id=instance.id,
                    kind="IP",
                    value_norm="192.0.2.99",
                    role="SOURCE_IP",
                    source="CATCHALL",
                    ref_table="catchall_bucket",
                    ref_id=str(source.id),
                    occurred_at=at,
                ),
                EntityMention(
                    instance_id=instance.id,
                    kind="IP",
                    value_norm="192.0.2.99",
                    role="SOURCE_IP",
                    source="PW_VALUE",
                    ref_table="pw_value_metric",
                    ref_id=str(value.id),
                    occurred_at=at,
                ),
            ]
        )
    await db_session.flush()

    statements: list[str] = []

    def capture(_conn, _cursor, statement, *_args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        timeline = await _timeline(
            db_session,
            instance.id,
            kind=EntityTimelineKind.IP,
            value="192.0.2.99",
            value_norm="192.0.2.99",
            start=PW_NOW,
            end=PW_NOW + timedelta(minutes=5 * bucket_count),
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert len(timeline.items) == bucket_count * 2
    assert len(statements) == 5


def test_deleting_a_watch_through_its_route_deletes_its_mentions(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        watch = await add_watch(session, instance)
        value = PwValue(
            watch_id=watch.id,
            watch_version=1,
            value_key="alice",
            component_0="alice",
            pinned=False,
            active=True,
            first_seen_at=PW_NOW,
            last_seen_at=PW_NOW,
            tracked_since=PW_NOW,
        )
        session.add(value)
        await session.flush()
        session.add(
            EntityMention(
                instance_id=instance.id,
                kind="USER",
                value_norm="alice",
                role="USER",
                source="PW_VALUE",
                ref_table="pw_value_metric",
                ref_id=str(value.id),
                occurred_at=PW_NOW,
            )
        )
        return watch.id

    watch_id = run(seed)
    writer = Client(
        Principal(
            subject="writer",
            permissions=frozenset(
                {"read:property_watches", "property_watch:write"}
            ),
        )
    )
    response = writer.delete(f"/api/v1/property-watches/{watch_id}")
    assert response.status_code == 204, response.text

    async def count(session):  # type: ignore[no-untyped-def]
        return await session.scalar(
            select(func.count()).select_from(EntityMention).where(
                EntityMention.source == "PW_VALUE"
            )
        )

    assert run(count) == 0
