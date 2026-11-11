"""SOC-16f3: history persists record entity mentions, insert-only.

Each test runs the family's real history persist, then checks the live parent
key, hydration, a history-only PARTIAL lane, a second persist that adds nothing,
and a later history persist that leaves a live parent's mentions in place.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select

from app.collectors.waf_endpoint_collector import WafCollectionReport
from app.core.config import get_settings
from app.history.planner import ScopeProgress
from app.history.waf import HistoryVolumeBucket
from app.models.catchall import CatchallBucket
from app.models.entity import EntityMention
from app.models.enums import SearchFamily
from app.models.ips import IpsEntity, IpsSourceBucket
from app.models.property_watch import PwBucket, PwValue, PwValueMetric
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket
from app.schemas.entity_timeline import EntityTimelineKind, EntityTimelineLane
from app.schemas.related_activity import RelatedSectionState
from app.security.rbac import Principal
from app.services.entity_timeline import REASON_NO_COVERAGE, EntityTimelineService
from tests.integration.factories import make_instance
from tests.integration.test_catchall_history import FIRST as CATCHALL_FIRST
from tests.integration.test_catchall_history import NOW as CATCHALL_NOW
from tests.integration.test_catchall_history import (
    always,
    decide,
    row,
    setup,
)
from tests.integration.test_catchall_history import (
    buckets as catchall_buckets,
)
from tests.integration.test_catchall_history import (
    collector as catchall_collector,
)
from tests.integration.test_catchall_history import (
    provider as catchall_provider,
)
from tests.integration.test_catchall_history import (
    scope as catchall_scope,
)
from tests.integration.test_ips_history import (
    BUCKET,
    MARGIN,
    SOURCE_ID,
    ScriptedIpsProvider,
    add_source,
    boundary_of,
    record_yes,
    scope_of,
    window_events,
)
from tests.integration.test_ips_history import (
    NOW as IPS_NOW,
)
from tests.integration.test_ips_history import (
    collect as ips_collect,
)
from tests.integration.test_ips_history import (
    hcfg as ips_hcfg,
)
from tests.integration.test_pw_collection import (
    NOW as PW_NOW,
)
from tests.integration.test_pw_collection import (
    STEP as PW_STEP,
)
from tests.integration.test_pw_collection import (
    USER_ACTION,
    add_watch,
    reload,
)
from tests.integration.test_pw_history_collection import (
    BOUNDARY,
    flat_provider,
    seed_reading,
    tick,
)
from tests.integration.test_pw_history_collection import (
    hcfg as pw_hcfg,
)
from tests.integration.test_waf_breakdown_collection import OUTCOMES_ON
from tests.integration.test_waf_keyed_collection import ScriptedWaf, make_source
from tests.integration.test_waf_productionization import STEP, T0, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
_SCOPE_FIELDS = (
    "state",
    "days_requested",
    "days_target",
    "target_start",
    "floor",
    "through",
    "anchor",
    "started_at",
    "finished_at",
    "reason",
    "reason_i18n",
    "last_error_class",
    "last_error_at",
    "progress",
)
_ACTION = [{"kind": "CUSTOM_PROPERTY", "name": "Action"}]


def _timeline_settings(**overrides: object):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(
        update={
            "waf_collection_enabled": True,
            "ips_collection_enabled": True,
            "catchall_collection_enabled": True,
            "pw_collection_enabled": True,
            **overrides,
        }
    )


async def _timeline(session, instance_id, *, kind, value, value_norm, start, end):  # type: ignore[no-untyped-def]
    return await EntityTimelineService(
        session,
        _timeline_settings(),
        READER,
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


def _lane(page, source: EntityTimelineLane):  # type: ignore[no-untyped-def]
    found = [lane for lane in page.lanes if lane.source is source]
    assert len(found) == 1
    return found[0]


def _identity(row: EntityMention) -> tuple[object, ...]:
    return (
        row.kind,
        row.value_norm,
        row.role,
        row.source,
        row.ref_table,
        row.ref_id,
        row.occurred_at,
    )


async def _mentions(session, instance_id, *, source: str | None = None) -> list[EntityMention]:  # type: ignore[no-untyped-def]
    statement = select(EntityMention).where(EntityMention.instance_id == instance_id)
    if source is not None:
        statement = statement.where(EntityMention.source == source)
    return list((await session.scalars(statement)).all())


def _snapshot_scope(scope) -> dict[str, object]:  # type: ignore[no-untyped-def]
    return {name: copy.deepcopy(getattr(scope, name)) for name in _SCOPE_FIELDS}


def _restore_scope(scope, saved: dict[str, object]) -> None:  # type: ignore[no-untyped-def]
    for name, value in saved.items():
        setattr(scope, name, copy.deepcopy(value))


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


def _mention_sql(statements: Sequence[str], verb: str) -> list[str]:
    return [
        sql
        for sql in statements
        if verb in sql.lower() and "entity_mention" in sql.lower()
    ]


def _assert_history_lane(page, source: EntityTimelineLane) -> None:  # type: ignore[no-untyped-def]
    lane = _lane(page, source)
    assert lane.state is RelatedSectionState.PARTIAL
    assert lane.reason_code == REASON_NO_COVERAGE
    assert lane.total is not None and lane.total > 0
    assert page.dangling == 0
    assert any(item.source is source for item in page.items)
    assert page.state is not RelatedSectionState.OK


def _assert_not_ok_empty(page, source: EntityTimelineLane) -> None:  # type: ignore[no-untyped-def]
    lane = _lane(page, source)
    assert lane.state is RelatedSectionState.NOT_COLLECTED
    assert lane.state is not RelatedSectionState.OK
    assert lane.total != 0


async def test_waf_history_breakdown_records_client_mentions_and_volume_writes_none(
    db_session,  # type: ignore[no-untyped-def]
) -> None:
    """100 requests split 10/90, so the stored client list holds both addresses."""
    settings = cfg(**OUTCOMES_ON, history_backfill_max_lower_bound_share=1.0)
    provider = ScriptedWaf({T0: [("/portal/login", "POST", 100)]})
    provider.derive_breakdowns()
    _profile, source, instance, factory = await make_source(
        db_session, provider, settings
    )
    collector = factory(T0 + STEP)
    keyed = await collector._observations_keyed(
        source, T0, T0 + STEP, int(STEP.total_seconds())
    )
    with _statements(db_session) as volume_sql:
        await collector.persist_history_volume(
            source,
            [HistoryVolumeBucket(batch=keyed.buckets[T0], collected_at=T0 + STEP)],
            floor=T0,
        )
        await db_session.flush()
    assert await _mentions(db_session, instance.id) == []
    assert _mention_sql(volume_sql, "insert") == []
    assert _mention_sql(volume_sql, "delete") == []

    progress = ScopeProgress(target_start=T0, started_at=T0, floor=T0, scan_cursor=None)
    step = await collector.history_breakdown_step(
        source,
        progress,
        boundary=T0 + STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    await db_session.flush()
    assert step.metrics_updated > 0

    endpoint = await db_session.scalar(
        select(WafEndpoint).where(
            WafEndpoint.log_source_id == source.id,
            WafEndpoint.normalized_endpoint == "/portal/login",
        )
    )
    assert endpoint is not None
    metric = await db_session.scalar(
        select(WafEndpointMetric).where(
            WafEndpointMetric.waf_endpoint_id == endpoint.id,
            WafEndpointMetric.bucket_start == T0,
        )
    )
    assert metric is not None and isinstance(metric.top_clients, list)
    mentions = await _mentions(db_session, instance.id, source="WAF_TOP")
    assert {_identity(item)[1] for item in mentions} == {"198.51.100.1", "198.51.100.2"}
    assert {(item.ref_table, item.ref_id, item.occurred_at) for item in mentions} == {
        ("waf_endpoint_metric", str(endpoint.id), T0)
    }
    assert {item.role for item in mentions} == {"SOURCE_IP"}
    assert {item.kind for item in mentions} == {"IP"}

    page = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.IP,
        value="198.51.100.2",
        value_norm="198.51.100.2",
        start=T0,
        end=T0 + STEP,
    )
    _assert_history_lane(page, EntityTimelineLane.WAF_TOP)
    absent = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.IP,
        value="203.0.113.9",
        value_norm="203.0.113.9",
        start=T0,
        end=T0 + STEP,
    )
    _assert_not_ok_empty(absent, EntityTimelineLane.WAF_TOP)

    before = {_identity(item) for item in mentions}
    with _statements(db_session) as replay_sql:
        again = await collector.history_breakdown_step(
            source,
            progress,
            boundary=T0 + STEP,
            search_budget=3,
            seconds_left=30,
            cadence_seconds=int(STEP.total_seconds()),
        )
        await db_session.flush()
    assert again.metrics_updated > 0
    assert _mention_sql(replay_sql, "delete") == []
    assert len(_mention_sql(replay_sql, "insert")) == 1
    assert {_identity(item) for item in await _mentions(db_session, instance.id)} == before

    live_at = T0 + 4 * STEP
    provider.volume[live_at] = [("/portal/login", "POST", 100)]
    provider.derive_breakdowns()
    live = factory(live_at + STEP)
    report = WafCollectionReport(instance.id)
    fetched = await live._fetch_per_bucket_control(
        source, live_at, live_at + STEP, report
    )
    await live._persist_interval(
        source,
        live_at,
        live_at + STEP,
        fetched,
        watermark_at=live_at,
        report=report,
    )
    await db_session.flush()
    stored = {_identity(item) for item in await _mentions(db_session, instance.id)}
    live_bucket = await db_session.get(WafSourceBucket, (source.id, live_at))
    assert live_bucket is not None and live_bucket.origin == "LIVE"
    assert any(item[6] == live_at and item[3] == "WAF_TOP" for item in stored)

    live_keyed = await collector._observations_keyed(
        source, live_at, live_at + STEP, int(STEP.total_seconds())
    )
    await collector.persist_history_volume(
        source,
        [HistoryVolumeBucket(batch=live_keyed.buckets[live_at], collected_at=live_at + STEP)],
        floor=live_at,
    )
    followed = await collector.history_breakdown_step(
        source,
        ScopeProgress(
            target_start=live_at, started_at=live_at, floor=live_at, scan_cursor=None
        ),
        boundary=live_at + STEP,
        search_budget=3,
        seconds_left=30,
        cadence_seconds=int(STEP.total_seconds()),
    )
    await db_session.flush()
    assert followed.buckets_updated > 0
    await db_session.refresh(live_bucket)
    assert live_bucket.origin == "LIVE"
    assert {_identity(item) for item in await _mentions(db_session, instance.id)} == stored


async def test_ips_history_records_top_and_entity_mentions(db_session) -> None:  # type: ignore[no-untyped-def]
    """A source with a live bucket is not eligible, so the scope opens first.

    The live bucket is then written while that scope is still READING (the
    switch-off tick takes the ordinary live window). Restoring the scope lets
    the reading tick persist the same parent and time.
    """
    instance = await make_instance(db_session)
    _profile, source = await add_source(db_session, instance, SOURCE_ID)
    boundary = boundary_of(IPS_NOW)
    start = boundary - MARGIN - BUCKET * 12
    provider = ScriptedIpsProvider(window_events(start, boundary), now=IPS_NOW)
    await record_yes(db_session, instance)
    scan = await ips_collect(db_session, instance, provider, ips_hcfg(), IPS_NOW)
    assert scan.history_buckets_written == 0
    assert scan.history_sources == 1
    opened = await scope_of(db_session, instance, str(SOURCE_ID))
    assert opened is not None and opened.state == "READING"
    saved = _snapshot_scope(opened)

    await ips_collect(
        db_session,
        instance,
        provider,
        ips_hcfg(history_backfill_enabled=False),
        IPS_NOW,
    )
    live_rows = (
        await db_session.scalars(
            select(IpsSourceBucket).where(
                IpsSourceBucket.log_source_id == source.id,
                IpsSourceBucket.origin == "LIVE",
            )
        )
    ).all()
    assert len(live_rows) == 1
    live_at = live_rows[0].bucket_start
    live_before = {
        _identity(item)
        for item in await _mentions(db_session, instance.id)
        if item.occurred_at == live_at
    }
    assert live_before

    scope = await scope_of(db_session, instance, str(SOURCE_ID))
    assert scope is not None
    _restore_scope(scope, saved)
    await db_session.commit()
    with _statements(db_session) as write_sql:
        written = await ips_collect(db_session, instance, provider, ips_hcfg(), IPS_NOW)
    assert written.history_buckets_written > 0
    assert _mention_sql(write_sql, "delete") == []
    assert len(_mention_sql(write_sql, "insert")) == 1

    history_rows = (
        await db_session.scalars(
            select(IpsSourceBucket).where(
                IpsSourceBucket.log_source_id == source.id,
                IpsSourceBucket.origin == "HISTORY",
            )
        )
    ).all()
    assert history_rows
    window_end = live_at
    window_start = min(item.bucket_start for item in history_rows)
    assert window_start < window_end
    assert all(item.bucket_start < live_at for item in history_rows)
    top = [
        item
        for item in await _mentions(db_session, instance.id, source="IPS_TOP")
        if window_start <= item.occurred_at < window_end
    ]
    entity_rows = [
        item
        for item in await _mentions(db_session, instance.id, source="IPS_ENTITY")
        if window_start <= item.occurred_at < window_end
    ]
    assert top and entity_rows
    assert {item.ref_table for item in top} == {"ips_source_bucket"}
    assert {item.ref_id for item in top} == {str(source.id)}
    assert {item.value_norm for item in top} == {"192.0.2.10"}
    assert {item.role for item in top} == {"SOURCE_IP"}
    destination = await db_session.scalar(
        select(IpsEntity).where(
            IpsEntity.log_source_id == source.id,
            IpsEntity.dimension == "DESTINATION_IP",
            IpsEntity.value_key == "198.51.100.20",
        )
    )
    assert destination is not None
    assert {item.ref_table for item in entity_rows} == {"ips_entity"}
    assert {item.ref_id for item in entity_rows} == {str(destination.id)}
    assert {item.value_norm for item in entity_rows} == {"198.51.100.20"}
    assert {item.role for item in entity_rows} == {"DESTINATION_IP"}

    top_page = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.IP,
        value="192.0.2.10",
        value_norm="192.0.2.10",
        start=window_start,
        end=window_end,
    )
    _assert_history_lane(top_page, EntityTimelineLane.IPS_TOP)
    entity_page = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.IP,
        value="198.51.100.20",
        value_norm="198.51.100.20",
        start=window_start,
        end=window_end,
    )
    _assert_history_lane(entity_page, EntityTimelineLane.IPS_ENTITY)
    absent = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.IP,
        value="203.0.113.50",
        value_norm="203.0.113.50",
        start=window_start,
        end=window_end,
    )
    _assert_not_ok_empty(absent, EntityTimelineLane.IPS_TOP)

    before = {_identity(item) for item in await _mentions(db_session, instance.id)}
    scope = await scope_of(db_session, instance, str(SOURCE_ID))
    assert scope is not None
    _restore_scope(scope, saved)
    await db_session.commit()
    replay = await ips_collect(db_session, instance, provider, ips_hcfg(), IPS_NOW)
    assert replay.history_buckets_written > 0
    assert {_identity(item) for item in await _mentions(db_session, instance.id)} == before
    await db_session.refresh(live_rows[0])
    assert live_rows[0].origin == "LIVE"
    assert {
        _identity(item)
        for item in await _mentions(db_session, instance.id)
        if item.occurred_at == live_at
    } == live_before


async def test_catchall_history_records_sender_mentions(db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.CATCHALL_SENDERS.value,
            cadence_seconds=3600,
            updated_at=CATCHALL_NOW,
        )
    )
    await db_session.commit()
    instance, _sources = await setup(db_session)
    prov = catchall_provider(always(row("fw-01", 50)))
    await catchall_collector(db_session, prov).collect(instance)
    live_before = {
        _identity(item) for item in await _mentions(db_session, instance.id)
    }
    assert live_before
    assert {item[6] for item in live_before} >= {CATCHALL_FIRST}

    await decide(db_session, instance)
    opened = await catchall_collector(db_session, prov).collect(instance)
    assert opened.history_buckets_written == 0
    saved = _snapshot_scope(await catchall_scope(db_session, instance))
    with _statements(db_session) as write_sql:
        written = await catchall_collector(db_session, prov).collect(instance)
    assert written.history_buckets_written > 0
    assert _mention_sql(write_sql, "delete") == []
    assert len(_mention_sql(write_sql, "insert")) == 1

    history = await catchall_buckets(db_session, "HISTORY")
    assert history
    window_start = min(item.bucket_start for item in history)
    # One bucket. The whole reading is more mentions than the page limit, and a
    # truncated page reports total=None on every lane.
    window_end = window_start + timedelta(seconds=300)
    assert window_end <= CATCHALL_FIRST
    mentions = [
        item
        for item in await _mentions(db_session, instance.id, source="CATCHALL")
        if window_start <= item.occurred_at < window_end
    ]
    assert mentions
    assert {item.value_norm for item in mentions} == {"fw-01"}
    assert {item.kind for item in mentions} == {"HOST"}
    assert {item.role for item in mentions} == {"HOST"}
    assert {item.ref_table for item in mentions} == {"catchall_bucket"}
    assert {item.ref_id for item in mentions} == {str(history[0].catchall_log_source_id)}

    page = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.HOST,
        value="fw-01",
        value_norm="fw-01",
        start=window_start,
        end=window_end,
    )
    _assert_history_lane(page, EntityTimelineLane.CATCHALL)
    absent = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.HOST,
        value="fw-absent",
        value_norm="fw-absent",
        start=window_start,
        end=window_end,
    )
    _assert_not_ok_empty(absent, EntityTimelineLane.CATCHALL)

    before = {_identity(item) for item in await _mentions(db_session, instance.id)}
    scope = await catchall_scope(db_session, instance)
    assert scope is not None
    _restore_scope(scope, saved)
    await db_session.flush()
    replay = await catchall_collector(db_session, prov).collect(instance)
    assert replay.history_buckets_written > 0
    assert {_identity(item) for item in await _mentions(db_session, instance.id)} == before

    live_bucket = (await catchall_buckets(db_session, "LIVE"))[0]
    payload = {
        column.name: getattr(live_bucket, column.name)
        for column in CatchallBucket.__table__.columns
    }
    payload["origin"] = "HISTORY"
    coll = catchall_collector(db_session, prov)
    with _statements(db_session) as conflict_sql:
        attempted = await coll._write_history_buckets(
            [payload], instance_id=instance.id
        )
        await db_session.flush()
    assert attempted == 1
    assert _mention_sql(conflict_sql, "delete") == []
    await db_session.refresh(live_bucket)
    assert live_bucket.origin == "LIVE"
    assert {_identity(item) for item in await _mentions(db_session, instance.id)} == before


async def test_property_watch_history_records_classified_values(db_session) -> None:  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    user = await add_watch(db_session, instance, history_days=8)
    combo = await add_watch(db_session, instance, properties=USER_ACTION, history_days=8)
    other = await add_watch(db_session, instance, properties=_ACTION, history_days=8)
    target = BOUNDARY - 12 * PW_STEP
    for watch in (user, combo, other):
        seed_reading(
            watch,
            target=target,
            floor=target,
            through=None,
            started=PW_NOW - timedelta(minutes=10),
            days=8,
        )
    await db_session.commit()
    settings = pw_hcfg(
        pw_max_searches_per_tick=80,
        pw_max_searches_per_watch_per_tick=40,
        pw_bootstrap_searches_per_tick=80,
    )
    provider = flat_provider({"alice": 10})
    with _statements(db_session) as write_sql:
        report = await tick(db_session, instance, provider, settings=settings)
    assert report.buckets_written == 36
    assert _mention_sql(write_sql, "delete") == []
    assert len(_mention_sql(write_sql, "insert")) == 1

    async def value_ids(watch_id):  # type: ignore[no-untyped-def]
        rows = (
            await db_session.scalars(select(PwValue).where(PwValue.watch_id == watch_id))
        ).all()
        return {str(item.id) for item in rows}

    user_ids = await value_ids(user.id)
    skipped_ids = await value_ids(combo.id) | await value_ids(other.id)
    mentions = await _mentions(db_session, instance.id, source="PW_VALUE")
    assert mentions
    assert {item.ref_id for item in mentions} <= user_ids
    assert not {item.ref_id for item in mentions} & skipped_ids
    assert {item.ref_table for item in mentions} == {"pw_value_metric"}
    assert {item.kind for item in mentions} == {"USER"}
    assert {item.value_norm for item in mentions} == {"alice"}
    assert {item.role for item in mentions} == {"USER"}
    for watch_id in (user.id, combo.id, other.id):
        metrics = await db_session.scalar(
            select(func.count())
            .select_from(PwValueMetric)
            .join(PwValue, PwValue.id == PwValueMetric.pw_value_id)
            .where(PwValue.watch_id == watch_id)
        )
        assert metrics and metrics > 0

    page = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="alice",
        value_norm="alice",
        start=target,
        end=target + PW_STEP,
    )
    _assert_history_lane(page, EntityTimelineLane.PW_VALUE)
    absent = await _timeline(
        db_session,
        instance.id,
        kind=EntityTimelineKind.USER,
        value="mallory",
        value_norm="mallory",
        start=target,
        end=target + PW_STEP,
    )
    _assert_not_ok_empty(absent, EntityTimelineLane.PW_VALUE)

    before = {_identity(item) for item in mentions}
    for watch in (user, combo, other):
        await reload(db_session, watch)
        seed_reading(
            watch,
            target=target,
            floor=target,
            through=None,
            started=PW_NOW - timedelta(minutes=10),
            days=8,
        )
    await db_session.commit()
    replay = await tick(db_session, instance, provider, settings=settings)
    assert replay.buckets_written > 0
    assert {
        _identity(item) for item in await _mentions(db_session, instance.id)
    } == before

    live_instance = await make_instance(db_session)
    live_watch = await add_watch(db_session, live_instance, history_days=1)
    await db_session.commit()
    await tick(
        db_session,
        live_instance,
        flat_provider({"alice": 10}),
        settings=pw_hcfg(pw_bootstrap_enabled=False),
    )
    live_bucket = await db_session.scalar(
        select(PwBucket).where(PwBucket.watch_id == live_watch.id)
    )
    assert live_bucket is not None and live_bucket.origin == "LIVE"
    live_keys = {
        _identity(item) for item in await _mentions(db_session, live_instance.id)
    }
    assert live_keys
    live_watch.watch_version = 2
    seed_reading(
        live_watch,
        target=BOUNDARY - 12 * PW_STEP,
        floor=BOUNDARY - 12 * PW_STEP,
        through=None,
        started=PW_NOW,
        days=1,
    )
    await db_session.commit()
    with _statements(db_session) as bump_sql:
        bumped = await tick(
            db_session, live_instance, flat_provider({"alice": 10}), settings=settings
        )
    assert bumped.buckets_written > 0
    assert _mention_sql(bump_sql, "delete") == []
    later = {_identity(item) for item in await _mentions(db_session, live_instance.id)}
    assert live_keys <= later
    assert len([item for item in later if item in live_keys]) == len(live_keys)
    assert len(later) > len(live_keys)
    survived = await _timeline(
        db_session,
        live_instance.id,
        kind=EntityTimelineKind.USER,
        value="alice",
        value_norm="alice",
        start=live_bucket.bucket_start,
        end=live_bucket.bucket_start + PW_STEP,
    )
    assert survived.dangling == 0
    assert any(item.source is EntityTimelineLane.PW_VALUE for item in survived.items)
