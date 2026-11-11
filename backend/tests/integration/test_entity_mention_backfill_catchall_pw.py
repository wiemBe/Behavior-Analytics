"""SOC-16f7: the CATCHALL and PW_VALUE backfill sources and their reader coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.collectors.catchall_collector import COLLECTOR_NAME as CATCHALL_COLLECTOR
from app.models.property_watch import PwValue, PwValueMetric
from app.schemas.entity_timeline import EntityTimelineKind
from app.services.entity_mention_folds import catchall_mentions, pw_value_mentions
from app.services.entity_mentions import EntityMentionRow, record_mentions
from app.services.locks import CollectorAdvisoryLock
from app.services.pw_pipeline import COLLECTOR_NAME as PW_COLLECTOR
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_entity_mention_backfill import (
    NOW,
    _name,
    counting,
    service,
    settings,
    shape,
    states,
    stored,
)
from tests.integration.test_pw_collection import add_watch
from tests.integration.test_soc16d1_entity_lanes import _catchall_bucket, _pw_bucket, _timeline

pytestmark = pytest.mark.integration

T = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
SENDERS = [
    {"identifier": "198.51.100.7", "events": 3},
    {"identifier": "Fw-01.Example.", "events": 1},
]
LOCKED = (("CATCHALL", CATCHALL_COLLECTOR), ("PW_VALUE", PW_COLLECTOR))


async def seed_catchall(  # type: ignore[no-untyped-def]
    session, instance, source, *, count=3, start=T, origins=("LIVE", "HISTORY")
):
    expected: list[EntityMentionRow] = []
    for index in range(count):
        at = start + timedelta(minutes=5 * index)
        bucket = _catchall_bucket(
            source.id, at, origin=origins[index % len(origins)], top=SENDERS
        )
        session.add(bucket)
        expected += catchall_mentions(
            instance.id,
            [
                {
                    "catchall_log_source_id": source.id,
                    "bucket_start": at,
                    "bucket_seconds": 300,
                    "top_senders": SENDERS,
                }
            ],
        )
    await session.flush()
    return expected


async def seed_pw(session, instance, *, buckets=2, start=T, keys=("alice", "CORP\\Bob")):  # type: ignore[no-untyped-def]
    """A username watch with values, plus watches/values the fold must reject."""
    expected: list[EntityMentionRow] = []
    watch = await add_watch(session, instance)
    sourceip = await add_watch(
        session, instance, properties=[{"kind": "NORMALIZED", "name": "sourceip"}]
    )
    combo = await add_watch(
        session,
        instance,
        properties=[
            {"kind": "NORMALIZED", "name": "username"},
            {"kind": "NORMALIZED", "name": "sourceip"},
        ],
    )
    other = await add_watch(
        session, instance, properties=[{"kind": "NORMALIZED", "name": "category"}]
    )
    plan = [(watch, key, False) for key in keys]
    plan += [(sourceip, "203.0.113.5", False), (combo, "alice|1.2.3.4", False)]
    plan += [(other, "x", False), (watch, "truncated-user", True)]
    for owner, key, truncated in plan:
        value = PwValue(
            watch_id=owner.id, watch_version=1, value_key=key, component_0=key,
            truncated=truncated, pinned=False, active=True,
            first_seen_at=start, last_seen_at=start, tracked_since=start,
        )
        session.add(value)
        await session.flush()
        for index in range(buckets):
            at = start + timedelta(minutes=5 * index)
            session.add(
                PwValueMetric(pw_value_id=value.id, bucket_start=at, events=2, bucket_seconds=300)
            )
            expected += pw_value_mentions(
                instance.id, owner.properties, pw_value_id=value.id, value_key=key,
                truncated=truncated, bucket_start=at, bucket_seconds=300,
            )
    await session.flush()
    return watch, expected


@pytest.mark.parametrize("source", ["CATCHALL", "PW_VALUE"])
async def test_exact_fold_rows_hydrate_and_rerun_writes_nothing(db_session, source) -> None:
    instance = await make_instance(db_session)
    log_source = await make_log_source(db_session, instance, qradar_id=7001, type_id=67)
    if source == "CATCHALL":
        expected = await seed_catchall(db_session, instance, log_source)
        probes = [
            (EntityTimelineKind.IP, "198.51.100.7"),
            (EntityTimelineKind.HOST, "fw-01.example"),
        ]
    else:
        _watch, expected = await seed_pw(db_session, instance)
        probes = [(EntityTimelineKind.USER, "alice"), (EntityTimelineKind.IP, "203.0.113.5")]
    assert expected
    await db_session.commit()
    name = await _name(db_session, instance)

    report = await service(db_session).run()

    assert report.instances[name][source].status == "COMPLETE"
    assert shape(await stored(db_session, source)) == shape(expected)
    assert report.instances[name][source].rows_written == len(expected)
    # The rejected watches / empty parents produced nothing; the hydrated items are not dangling.
    for kind, value in probes:
        page = await _timeline(
            db_session, instance.id, kind=kind, value=value, value_norm=value.lower(),
            start=T - timedelta(hours=1), end=T + timedelta(hours=1),
        )
        assert [i for i in page.items if i.source.value == source], (source, value)
        assert page.dangling == 0

    from sqlalchemy import delete

    from app.models.entity import EntityMentionBackfill

    await db_session.execute(delete(EntityMentionBackfill))
    await db_session.commit()
    again = await service(db_session).run()
    assert again.instances[name][source].rows_written == 0
    assert again.instances[name][source].status == "COMPLETE"
    assert shape(await stored(db_session, source)) == shape(expected)


async def test_pw_filter_pages_only_classifiable_values(db_session) -> None:
    instance = await make_instance(db_session)
    _watch, expected = await seed_pw(db_session, instance, buckets=1)
    await db_session.commit()
    svc = service(db_session)
    await svc._ensure_rows([instance.id])
    batch = await svc._pw_value(instance.id, NOW, None, 500)
    # username x2 + sourceip x1; combo, other watch and the truncated value never leave SQL.
    assert batch.parents == 3 == len(expected)


@pytest.mark.parametrize("source", ["CATCHALL", "PW_VALUE"])
async def test_snapshot_bound_resume_and_live_mentions_untouched(db_session, source) -> None:
    instance = await make_instance(db_session)
    log_source = await make_log_source(db_session, instance, qradar_id=7002, type_id=67)
    before = NOW - timedelta(hours=3)
    late = NOW + timedelta(hours=1)
    if source == "CATCHALL":
        expected = await seed_catchall(
            db_session, instance, log_source, count=5, start=before, origins=("LIVE",)
        )
        await seed_catchall(db_session, instance, log_source, count=1, start=late)
        per_parent = len(SENDERS)
    else:
        _w, expected = await seed_pw(db_session, instance, buckets=5, start=before, keys=("alice",))
        await seed_pw(db_session, instance, buckets=1, start=late, keys=("late-user",))
        per_parent = 1
    # A live mention for the first parent already exists and must be neither duplicated nor lost.
    await record_mentions(db_session, expected[:1])
    await db_session.commit()
    name = await _name(db_session, instance)
    svc = service(
        db_session,
        entity_mention_backfill_batch_size=2,
        entity_mention_backfill_max_batches_per_run=1,
    )
    runs = []
    for _ in range(30):
        report = await svc.run()
        result = report.instances[name][source]
        if result.batches:
            runs.append(result)
        if report.complete:
            break
    else:
        pytest.fail("not complete")
    rows = await stored(db_session, source)
    assert shape(rows) == shape(expected)  # nothing from after `until`, no duplicates
    assert all(r.occurred_at < NOW for r in rows)
    assert runs[-1].status == "COMPLETE" and len(runs) >= 3
    row = (await states(db_session, instance.id))[source]
    assert row.rows_written == len(expected) - 1  # the pre-existing mention is not counted
    assert row.cursor["until"] == NOW.isoformat() and per_parent >= 1


@pytest.mark.parametrize("source", ["CATCHALL", "PW_VALUE"])
async def test_statements_per_batch_are_constant(db_session, source) -> None:
    counts = {}
    for parents in (1, 50):
        await db_session.execute(text("DELETE FROM entity_mention_backfill"))
        await db_session.execute(text("DELETE FROM catchall_bucket"))
        await db_session.execute(text("DELETE FROM pw_value_metric"))
        await db_session.commit()
        instance = await make_instance(db_session)
        log_source = await make_log_source(
            db_session, instance, qradar_id=7100 + parents, type_id=67
        )
        if source == "CATCHALL":
            await seed_catchall(db_session, instance, log_source, count=parents)
        else:
            await seed_pw(db_session, instance, buckets=parents, keys=("alice",))
        await db_session.commit()
        svc = service(db_session)
        await svc._ensure_rows([instance.id])
        with counting(db_session) as statements:
            await svc._batch(instance.id, source, NOW, None, 500, {"until": NOW.isoformat()})
        counts[parents] = [s.split()[0] for s in statements]
    assert counts[1] == counts[50] == ["SELECT", "INSERT", "UPDATE"]


@pytest.mark.parametrize(("source", "lock_name"), LOCKED)
async def test_held_collector_lock_blocks_only_that_source(db_session, source, lock_name) -> None:
    instance = await make_instance(db_session)
    log_source = await make_log_source(db_session, instance, qradar_id=7003, type_id=67)
    await seed_catchall(db_session, instance, log_source, count=2)
    await seed_pw(db_session, instance, buckets=1)
    await db_session.commit()
    instance_id = instance.id
    name = await _name(db_session, instance)
    async with CollectorAdvisoryLock(db_session, settings(), instance_id, lock_name) as held:
        assert held is True
        blocked = await service(db_session).run()
    result = blocked.instances[name]
    assert result[source].busy and result[source].status == "RUNNING"
    assert await stored(db_session, source) == []
    other = "PW_VALUE" if source == "CATCHALL" else "CATCHALL"
    assert result[other].status == "COMPLETE" and await stored(db_session, other)
    resumed = await service(db_session).run()
    assert resumed.instances[name][source].status == "COMPLETE"
    assert await stored(db_session, source)


# ------------------------------------------------------------------- reader
async def _lane_state(session, instance_id, kind, value, source):  # type: ignore[no-untyped-def]
    page = await _timeline(
        session, instance_id, kind=kind, value=value, value_norm=value,
        start=T, end=T + timedelta(minutes=5),
    )
    lane = next(lane for lane in page.lanes if lane.source.value == source)
    return lane.state.value, lane.reason_code, lane.total


async def test_history_only_window_is_ok_only_after_complete(db_session) -> None:
    instance = await make_instance(db_session)
    log_source = await make_log_source(db_session, instance, qradar_id=7004, type_id=67)
    watch = await add_watch(db_session, instance)
    db_session.add_all(
        [
            _catchall_bucket(log_source.id, T, origin="HISTORY"),
            _pw_bucket(watch, T, origin="HISTORY"),
        ]
    )
    await db_session.commit()
    cases = [
        ("CATCHALL", EntityTimelineKind.HOST, "missing-host"),
        ("PW_VALUE", EntityTimelineKind.USER, "missing-user"),
    ]
    for source, kind, value in cases:
        state, reason, total = await _lane_state(db_session, instance.id, kind, value, source)
        assert (state, reason, total) == (
            "PARTIAL", "entity_timeline.reason.index_incomplete", None,
        ), source

    await service(db_session).run()

    for source, kind, value in cases:
        state, _reason, total = await _lane_state(db_session, instance.id, kind, value, source)
        assert (state, total) == ("OK", 0), source
