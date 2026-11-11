"""SOC-16f4: the entity-mention backfill service, beat task and operator CLI on PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, func, null, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cli import entity_mentions as cli
from app.collectors.explanation_collector import EXPLANATION_COLLECTOR
from app.collectors.offense_collector import COLLECTOR_NAME as OFFENSE_COLLECTOR
from app.core.config import Settings
from app.models import Base
from app.models.entity import EntityMention, EntityMentionBackfill
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IpsDimension,
    Severity,
)
from app.models.explanation import AnomalyExplanation, AnomalyExplanationContributor
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket
from app.models.log_source import LogSourceAnomaly
from app.models.waf import WafEndpointMetric
from app.services import entity_mention_backfill as backfill
from app.services.entity_mention_backfill import (
    LOCK_INSTANCE_ID,
    LOCK_NAME,
    EntityMentionBackfillService,
)
from app.services.entity_mention_folds import (
    explanation_mentions,
    ips_entity_mentions,
    ips_top_mentions,
    offense_mentions,
    waf_top_mentions,
)
from app.services.entity_mentions import EntityMentionRow, record_mentions
from app.services.locks import CollectorAdvisoryLock, collector_lock_key
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_histogram import endpoint as make_endpoint
from tests.integration.test_entity_mention_writers import (
    add_source,
    cfg,
    ips_collect,
    waf_env,
    waf_provider,
    waf_run,
)
from tests.integration.test_entity_mention_writers import key as mention_key
from tests.integration.test_entity_mention_writers import mentions as all_mentions
from tests.integration.test_entity_timeline import (
    END,
    READER,
    START,
    T0,
    TimelineClient,
    _ips_bucket,
    _snapshot,
    _url,
)
from tests.integration.test_fleet_api import run
from tests.integration.test_waf_breakdown_collection import OUTCOMES_ON

pytestmark = pytest.mark.integration

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="
NOW = T0 + timedelta(days=2)
SOURCES = ("EXPLANATION", "WAF_TOP", "IPS_TOP", "IPS_ENTITY", "OFFENSE")


def settings(**kw: Any) -> Settings:
    return Settings(**kw)


def service(session, *, clock: Callable[[], datetime] | None = None, **kw: Any):  # type: ignore[no-untyped-def]
    return EntityMentionBackfillService(
        session, settings=settings(**kw), clock=clock or (lambda: NOW)
    )


def shape(rows):  # type: ignore[no-untyped-def]
    return sorted(
        (
            r.kind, r.value_norm, r.role, r.source, r.ref_table, r.ref_id,
            r.occurred_at, r.occurred_end,
        )
        for r in rows
    )


async def stored(session, source: str | None = None):  # type: ignore[no-untyped-def]
    query = select(EntityMention).execution_options(populate_existing=True)
    if source is not None:
        query = query.where(EntityMention.source == source)
    return list((await session.scalars(query)).all())


async def states(session, instance_id=None):  # type: ignore[no-untyped-def]
    query = select(EntityMentionBackfill).execution_options(populate_existing=True)
    if instance_id is not None:
        query = query.where(EntityMentionBackfill.instance_id == instance_id)
    return {r.source: r for r in (await session.scalars(query)).all()}


@contextmanager
def counting(session) -> Iterator[list[str]]:  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(_conn, _cursor, statement, *_rest) -> None:  # type: ignore[no-untyped-def]
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


# ----------------------------------------------------------------- seeding
class World:
    """Stored parents of every in-scope source for one instance, and their live-fold rows."""

    def __init__(self) -> None:
        self.expected: dict[str, list[EntityMentionRow]] = {s: [] for s in SOURCES}


async def seed_explanation(session, instance, source, world: World, *, created_at=None):  # type: ignore[no-untyped-def]
    anomaly = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        detected_at=T0,
        anomaly_start=T0,
        anomaly_end=T0 + timedelta(minutes=5),
        state=AnomalyState.OPEN,
        evidence_status=EvidenceStatus.COMPLETE,
        details={},
    )
    session.add(anomaly)
    await session.flush()
    package = AnomalyExplanation(
        anomaly_id=anomaly.id,
        status=EvidenceStatus.COMPLETE,
        anomaly_window_start=T0,
        anomaly_window_end=T0 + timedelta(minutes=5),
        baseline_window_start=T0 - timedelta(hours=1),
        baseline_window_end=T0,
        comparison_strategy="test",
        anomaly_total_events=100,
        baseline_total_events=100,
        query_provenance={},
    )
    session.add(package)
    await session.flush()
    contributors = []
    for dimension, value in (
        ("source_ip", "203.0.113.1"),
        ("destination_ip", "10.0.0.5"),
        ("username", "CORP\\Alice"),
        ("event_name", "not an entity"),
    ):
        row = AnomalyExplanationContributor(
            explanation_id=package.id, dimension=dimension, value=value
        )
        if created_at is not None:
            row.created_at = created_at
        session.add(row)
        contributors.append(row)
    await session.flush()
    world.expected["EXPLANATION"] += [
        m
        for c in contributors
        for m in explanation_mentions(
            instance.id,
            [{"id": c.id, "dimension": c.dimension, "value": c.value}],
            anomaly_window_start=T0,
            anomaly_window_end=T0 + timedelta(minutes=5),
        )
    ]
    return contributors


async def seed_waf(session, instance, source, world: World, *, buckets=3, start=None, **kw):  # type: ignore[no-untyped-def]
    ep = make_endpoint(instance.id, source.id, path=f"/p{uuid.uuid4().hex[:6]}")
    session.add(ep)
    await session.flush()
    first = start or T0
    for index in range(buckets):
        bucket = first + timedelta(minutes=5 * index)
        clients = [{"source_ip": f"198.51.100.{index + 1}", "requests": 9}]
        session.add(
            WafEndpointMetric(
                waf_endpoint_id=ep.id,
                bucket_start=bucket,
                request_count=10,
                top_clients=clients,
            )
        )
        world.expected["WAF_TOP"] += waf_top_mentions(
            instance.id, waf_endpoint_id=ep.id, bucket_start=bucket, top_clients=clients
        )
    await session.flush()
    return ep


async def seed_ips_top(  # type: ignore[no-untyped-def]
    session, instance, source, world: World, *, origins=("LIVE", "HISTORY"), start=None
):
    first = start or T0
    for index, origin in enumerate(origins):
        bucket = _ips_bucket(source.id)
        bucket.bucket_start = first + timedelta(minutes=5 * index)
        bucket.origin = origin
        bucket.contributors = {
            "SOURCE_IP": [{"source_ip": f"203.0.113.{index + 10}", "events": 5}],
            "PORT_FANOUT": [{"source_ip": f"203.0.113.{index + 20}", "events": 4}],
        }
        session.add(bucket)
        world.expected["IPS_TOP"] += ips_top_mentions(
            instance.id,
            log_source_id=source.id,
            bucket_start=bucket.bucket_start,
            contributors=bucket.contributors,
        )
    await session.flush()


async def seed_ips_entity(session, instance, source, world: World, *, start=None):  # type: ignore[no-untyped-def]
    first = start or T0
    for dimension, value in (
        (IpsDimension.DESTINATION_IP, "10.9.8.7"),
        (IpsDimension.DESTINATION_PORT, "443"),
    ):
        entity = IpsEntity(
            instance_id=instance.id,
            log_source_id=source.id,
            dimension=dimension,
            value_key=value,
            first_seen_at=T0,
            last_seen_at=T0,
            active=True,
        )
        session.add(entity)
        await session.flush()
        for index in range(2):
            bucket = first + timedelta(minutes=5 * index)
            session.add(
                IpsEntityMetric(
                    ips_entity_id=entity.id, bucket_start=bucket, events=3, bucket_seconds=300
                )
            )
            if dimension is IpsDimension.DESTINATION_IP:
                world.expected["IPS_ENTITY"] += ips_entity_mentions(
                    instance.id, ips_entity_id=entity.id, bucket_start=bucket, value_key=value
                )
    await session.flush()


async def seed_offense(session, instance, world: World, *, captured_at=None):  # type: ignore[no-untyped-def]
    old = _snapshot(instance.id, 7001)
    old.captured_at = T0 + timedelta(hours=1)
    old.usernames = ["stale-user"]
    new = _snapshot(instance.id, 7001)
    new.captured_at = captured_at or (T0 + timedelta(hours=2))
    new.usernames = ["CORP\\Bob"]
    new.offense_source = "198.51.100.9"
    new.offense_type = 1
    other = _snapshot(instance.id, 7002)
    other.captured_at = captured_at or (T0 + timedelta(hours=2))
    session.add_all([old, new, other])
    await session.flush()
    for row in (new, other):
        world.expected["OFFENSE"] += offense_mentions(
            instance.id,
            qradar_offense_id=row.qradar_offense_id,
            captured_at=row.captured_at,
            usernames=list(row.usernames),
            offense_source=row.offense_source,
            offense_type=row.offense_type,
            start_time=row.start_time,
            last_updated_time=row.last_updated_time,
            close_time=row.close_time,
        )


async def seed_world(session):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    source = await make_log_source(session, instance, name="edge-fw", identifier="192.0.2.1")
    world = World()
    await seed_explanation(session, instance, source, world)
    await seed_waf(session, instance, source, world)
    await seed_ips_top(session, instance, source, world)
    await seed_ips_entity(session, instance, source, world)
    await seed_offense(session, instance, world)
    await session.commit()
    return instance, source, world


# ----------------------------------------------------------------- the tests
async def test_every_source_matches_the_live_fold_exactly(db_session) -> None:
    instance, _source, world = await seed_world(db_session)
    assert await stored(db_session) == []

    report = await service(db_session).run()

    assert not report.busy and report.complete and not report.failed
    for source in SOURCES:
        assert shape(await stored(db_session, source)) == shape(world.expected[source]), source
        assert world.expected[source], f"{source} fixture must produce mentions"
    result = report.instances[(await _name(db_session, instance))]
    assert {s: result[s].status for s in SOURCES} == dict.fromkeys(SOURCES, "COMPLETE")
    assert result["OFFENSE"].rows_written == len(world.expected["OFFENSE"])
    # Only the newest snapshot of offense 7001 is read.
    assert not [r for r in await stored(db_session, "OFFENSE") if r.value_norm == "stale-user"]
    # Both IPS origins and the DESTINATION_IP entity only.
    assert {r.occurred_at for r in await stored(db_session, "IPS_TOP")} == {
        T0,
        T0 + timedelta(minutes=5),
    }
    assert {r.value_norm for r in await stored(db_session, "IPS_ENTITY")} == {"10.9.8.7"}
    # Not an indexed explanation dimension.
    assert "not an entity" not in {r.value_norm for r in await stored(db_session, "EXPLANATION")}


async def _name(session, instance) -> str:  # type: ignore[no-untyped-def]
    from app.models.instance import QRadarInstance

    return str(
        await session.scalar(select(QRadarInstance.name).where(QRadarInstance.id == instance.id))
    )


def test_backfilled_mentions_hydrate_through_the_timeline(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance, _source, world = await seed_world(session)
        return instance.id, world

    instance_id, _world = run(seed)

    async def backfill_all(session):  # type: ignore[no-untyped-def]
        return (await service(session).run()).complete

    assert run(backfill_all) is True
    client = TimelineClient(READER)
    probes = {
        "EXPLANATION": "203.0.113.1",
        "WAF_TOP": "198.51.100.1",
        "IPS_TOP": "203.0.113.10",
        "IPS_ENTITY": "10.9.8.7",
        "OFFENSE": "198.51.100.9",
    }
    for source, value in probes.items():
        url = _url(instance_id, value=value, start=START.isoformat(), end=END.isoformat())
        body = client.get(url).json()
        items = [i for i in body["items"] if i["source"] == source]
        assert items, (source, body)
        assert all(i["href"] for i in items), source
        assert body["dangling"] == 0, (source, body)


async def test_waf_metric_without_a_stored_list_yields_no_mention(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    world = World()
    ep = await seed_waf(db_session, instance, source, world, buckets=1)
    for index, top in enumerate((None, null())):
        db_session.add(
            WafEndpointMetric(
                waf_endpoint_id=ep.id,
                bucket_start=T0 + timedelta(hours=1 + index),
                request_count=10,
                top_clients=top,
            )
        )
    await db_session.commit()

    report = await service(db_session).run()

    assert report.instances[await _name(db_session, instance)]["WAF_TOP"].status == "COMPLETE"
    rows = await stored(db_session, "WAF_TOP")
    assert shape(rows) == shape(world.expected["WAF_TOP"])
    assert len(rows) == 1  # JSON null and SQL NULL rows produced nothing


async def test_resumes_in_bounded_runs_without_duplicates(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    world = World()
    await seed_waf(db_session, instance, source, world, buckets=5)
    await db_session.commit()
    name = await _name(db_session, instance)
    svc = service(
        db_session,
        entity_mention_backfill_batch_size=2,
        entity_mention_backfill_max_batches_per_run=1,
    )

    waf_runs: list[Any] = []
    cursors: list[Any] = []
    for _ in range(12):
        report = await svc.run()
        assert report.batches <= 1
        result = report.instances[name]["WAF_TOP"]
        if result.batches:
            waf_runs.append(result)
            cursors.append((await states(db_session, instance.id))["WAF_TOP"].cursor)
        if report.complete:
            break
    else:
        pytest.fail("the backfill did not complete")

    # 5 parents / batch of 2: three runs touch WAF_TOP (2 + 2 + 1); the empty sources take
    # one batch each before it, so they are not counted here.
    assert [r.batches for r in waf_runs] == [1, 1, 1]
    assert [r.rows_written for r in waf_runs] == [2, 2, 1]
    assert [r.status for r in waf_runs] == ["RUNNING", "RUNNING", "COMPLETE"]
    afters = [c["after"] for c in cursors]
    assert len({tuple(a) for a in afters}) == 3 and afters == sorted(afters, key=lambda a: a[1])
    assert len({c["until"] for c in cursors}) == 1
    assert len(await stored(db_session, "WAF_TOP")) == 5
    row = (await states(db_session, instance.id))["WAF_TOP"]
    assert row.rows_written == 5 and row.status == "COMPLETE" and row.completed_at == NOW


async def test_a_parent_stored_after_the_snapshot_bound_is_not_read(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    world = World()
    before = NOW - timedelta(hours=3)
    await seed_explanation(db_session, instance, source, world, created_at=before)
    late = NOW + timedelta(hours=1)
    await seed_explanation(db_session, instance, source, World(), created_at=late)
    await seed_waf(db_session, instance, source, world, buckets=1, start=before)
    await seed_waf(db_session, instance, source, World(), buckets=1, start=NOW + timedelta(hours=1))
    await seed_ips_top(db_session, instance, source, world, origins=("LIVE",), start=before)
    await seed_ips_top(
        db_session, instance, source, World(), origins=("LIVE",), start=NOW + timedelta(hours=1)
    )
    await seed_ips_entity(db_session, instance, source, world, start=before)
    await seed_offense(db_session, instance, world, captured_at=before)
    late_snapshot = _snapshot(instance.id, 7999)
    late_snapshot.captured_at = NOW + timedelta(hours=1)
    late_snapshot.usernames = ["late-user"]
    db_session.add(late_snapshot)
    await db_session.commit()

    # The bound is fixed when each source starts: NOW.
    await service(db_session).run()

    after_bound = NOW
    for row in await stored(db_session):
        assert row.occurred_at < after_bound, (row.source, row.value_norm)
    assert "late-user" not in {r.value_norm for r in await stored(db_session, "OFFENSE")}
    assert len(await stored(db_session, "EXPLANATION")) == 3  # the late package adds none
    assert len(await stored(db_session, "IPS_TOP")) == 2  # nor the late bucket
    waf = await stored(db_session, "WAF_TOP")
    assert len(waf) == 1  # only the parent stored before `until`
    assert (await states(db_session, instance.id))["WAF_TOP"].cursor["until"] == NOW.isoformat()


async def test_keyset_survives_a_parent_vanishing_behind_the_cursor(db_session) -> None:
    """OFFSET pagination would shift by one and skip a parent; the keyset position does not."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    world = World()
    ep = await seed_waf(db_session, instance, source, world, buckets=4)
    await db_session.commit()
    name = await _name(db_session, instance)
    svc = service(db_session, entity_mention_backfill_batch_size=2)
    # EXPLANATION (empty), then the first WAF_TOP batch only: parents 1 and 2.
    first = await svc.run(max_batches=2)
    assert first.instances[name]["WAF_TOP"].status == "RUNNING"
    # Parent 1 (behind the cursor) is deleted, as retention or a replace would.
    await db_session.execute(
        text("DELETE FROM waf_endpoint_metric WHERE waf_endpoint_id = :id AND bucket_start = :t"),
        {"id": ep.id, "t": T0},
    )
    await db_session.commit()

    await svc.run()

    values = {r.value_norm for r in await stored(db_session, "WAF_TOP")}
    assert values == {f"198.51.100.{i}" for i in range(1, 5)}
    assert len(await stored(db_session, "WAF_TOP")) == 4


async def test_second_full_run_writes_nothing_and_stays_complete(db_session) -> None:
    instance, _source, world = await seed_world(db_session)
    name = await _name(db_session, instance)
    await service(db_session).run()
    total = len(await stored(db_session))
    assert total == sum(len(v) for v in world.expected.values())

    again = await service(db_session).run()
    assert again.batches == 0
    assert {r.status for r in again.instances[name].values()} == {"COMPLETE"}

    # Lose the resume state: every parent is read again; nothing is inserted or counted.
    from sqlalchemy import delete

    await db_session.execute(delete(EntityMentionBackfill))
    await db_session.commit()
    third = await service(db_session).run()
    assert all(r.rows_written == 0 for r in third.instances[name].values())
    assert third.complete and len(await stored(db_session)) == total
    assert {s.rows_written for s in (await states(db_session)).values()} == {0}


async def test_existing_live_mentions_are_kept_and_not_duplicated(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    world = World()
    ep = await seed_waf(db_session, instance, source, world, buckets=1)
    await db_session.commit()
    own = world.expected["WAF_TOP"][0]
    # The live writer already indexed this parent, with one more address than is stored now.
    stale = EntityMentionRow(
        instance_id=instance.id,
        kind="IP",
        value_norm="203.0.113.99",
        role="SOURCE_IP",
        source="WAF_TOP",
        ref_table="waf_endpoint_metric",
        ref_id=str(ep.id),
        occurred_at=own.occurred_at,
    )
    await record_mentions(db_session, [own, stale])
    await db_session.commit()

    report = await service(db_session).run()

    rows = await stored(db_session, "WAF_TOP")
    assert sorted(r.value_norm for r in rows) == sorted([own.value_norm, "203.0.113.99"])
    assert report.instances[await _name(db_session, instance)]["WAF_TOP"].rows_written == 0


async def test_busy_lock_changes_nothing(db_session) -> None:
    await seed_world(db_session)
    holder_settings = settings()
    async with CollectorAdvisoryLock(
        db_session, holder_settings, LOCK_INSTANCE_ID, LOCK_NAME
    ) as held:
        assert held is True
        report = await service(db_session).run()
    assert report.busy and report.instances == {} and report.batches == 0
    assert await states(db_session) == {}
    assert await stored(db_session) == []
    # Released: the next run goes ahead.
    assert (await service(db_session).run()).busy is False


async def test_a_failing_source_is_failed_short_and_retried(
    db_session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    instance, _source, world = await seed_world(db_session)
    name = await _name(db_session, instance)
    real = EntityMentionBackfillService._waf_top

    async def boom(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret-token-198.51.100.1 /private/path")

    monkeypatch.setattr(EntityMentionBackfillService, "_waf_top", boom)
    with caplog.at_level("WARNING"):
        report = await service(db_session).run()

    assert report.failed
    assert report.instances[name]["WAF_TOP"].status == "FAILED"
    row = (await states(db_session, instance.id))["WAF_TOP"]
    assert row.status == "FAILED" and row.rows_written == 0
    assert row.last_error == f"RuntimeError: {backfill.FAILED_TEXT}"
    assert "secret-token" not in row.last_error and "198.51" not in row.last_error
    warnings = [r for r in caplog.records if "backfill batch failed" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].error_class == "RuntimeError"  # type: ignore[attr-defined]
    assert "secret-token" not in str(warnings[0].__dict__)
    # The other sources still advanced.
    other = {s: r.status for s, r in report.instances[name].items() if s != "WAF_TOP"}
    assert set(other.values()) == {"COMPLETE"}
    assert len(await stored(db_session, "IPS_TOP")) == len(world.expected["IPS_TOP"])
    assert await stored(db_session, "WAF_TOP") == []
    cursor_before = row.cursor

    # The next run retries it from the same cursor.
    monkeypatch.setattr(EntityMentionBackfillService, "_waf_top", real)
    retry = await service(db_session).run()
    assert retry.instances[name]["WAF_TOP"].status == "COMPLETE"
    done = (await states(db_session, instance.id))["WAF_TOP"]
    assert done.last_error is None and done.cursor["until"] == cursor_before["until"]
    assert len(await stored(db_session, "WAF_TOP")) == len(world.expected["WAF_TOP"])


async def test_a_failed_insert_rolls_the_whole_batch_back(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance, _source, _world = await seed_world(db_session)
    instance_id = instance.id  # a rollback expires the ORM object
    real = backfill._insert_mentions

    async def broken(session, mentions):  # type: ignore[no-untyped-def]
        mentions = list(mentions)
        if mentions and mentions[0].source == "IPS_TOP":
            await real(session, mentions)  # rows are written, then the batch fails
            raise RuntimeError("late failure")
        return await real(session, mentions)

    monkeypatch.setattr(backfill, "_insert_mentions", broken)
    await service(db_session).run()

    assert (await states(db_session, instance_id))["IPS_TOP"].status == "FAILED"
    assert await stored(db_session, "IPS_TOP") == []
    assert (await states(db_session, instance_id))["IPS_TOP"].cursor.get("after") is None


async def test_statements_per_batch_are_constant(db_session) -> None:
    counts: dict[int, list[str]] = {}
    for parents in (1, 50):
        await db_session.execute(text("DELETE FROM waf_endpoint"))
        await db_session.execute(text("DELETE FROM entity_mention_backfill"))
        await db_session.commit()
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance, qradar_id=100 + parents)
        await seed_waf(db_session, instance, source, World(), buckets=parents)
        await db_session.commit()
        svc = service(db_session)
        await svc._ensure_rows([instance.id])
        cursor = {"until": NOW.isoformat()}
        with counting(db_session) as statements:
            complete, inserted, _ = await svc._batch(instance.id, "WAF_TOP", NOW, None, 500, cursor)
        assert complete and inserted == parents
        counts[parents] = statements
    assert len(counts[1]) == len(counts[50]) == 3
    kinds = [s.split()[0] for s in counts[50]]
    assert kinds == ["SELECT", "INSERT", "UPDATE"]


async def test_log_source_is_complete_and_every_source_gets_a_row(db_session) -> None:
    instance = await make_instance(db_session)
    await db_session.commit()
    await service(db_session).run()
    rows = await states(db_session, instance.id)
    # SOC-16f7: CATCHALL and PW_VALUE now have rows (they had none in SOC-16f4).
    assert set(rows) == {"LOG_SOURCE", *SOURCES, "CATCHALL", "PW_VALUE"}
    log_source = rows["LOG_SOURCE"]
    assert (log_source.status, log_source.rows_written) == ("COMPLETE", 0)
    assert log_source.completed_at == NOW
    # No parents: every other source completes with nothing written.
    assert {r.rows_written for r in rows.values()} == {0}


async def test_disabled_instance_gets_no_rows(db_session) -> None:
    instance = await make_instance(db_session)
    instance.enabled = False
    await db_session.commit()
    report = await service(db_session).run()
    assert report.instances == {} and await states(db_session) == {}


async def test_task_runs_the_service_and_respects_the_switch(
    db_session, db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    _instance, _source, world = await seed_world(db_session)
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        monkeypatch.setattr(
            tasks, "get_settings", lambda: Settings(entity_mention_backfill_enabled=False)
        )
        off = await tasks.backfill_entity_mentions()
        assert off["status"] == "disabled" and await states(db_session) == {}

        monkeypatch.setattr(tasks, "get_settings", lambda: Settings())
        on = await tasks.backfill_entity_mentions()
    finally:
        await engine.dispose()
    assert on["status"] == "ok" and on["busy"] is False and on["batches"] >= len(SOURCES)
    sources = next(iter(on["instances"].values()))
    assert sources["WAF_TOP"]["status"] == "COMPLETE"
    assert sources["WAF_TOP"]["rows_written"] == len(world.expected["WAF_TOP"])
    assert await db_session.scalar(select(func.count()).select_from(EntityMention)) == sum(
        len(v) for v in world.expected.values()
    )


# ------------------------------------------------- D-66: the collector locks
LOCKED = (("EXPLANATION", EXPLANATION_COLLECTOR), ("OFFENSE", OFFENSE_COLLECTOR))


@pytest.mark.parametrize(("source", "lock_name"), LOCKED)
async def test_a_held_collector_lock_blocks_only_that_source_and_it_resumes(
    db_session, source: str, lock_name: str
) -> None:
    instance, _source, world = await seed_world(db_session)
    instance_id = instance.id
    name = await _name(db_session, instance)
    async with CollectorAdvisoryLock(db_session, settings(), instance_id, lock_name) as held:
        assert held is True
        blocked = await service(db_session).run()

    result = blocked.instances[name]
    assert result[source].busy is True and result[source].status == "RUNNING"
    assert result[source].batches == 0 and result[source].rows_written == 0
    assert not blocked.failed
    assert await stored(db_session, source) == []
    row = (await states(db_session, instance_id))[source]
    assert row.status == "RUNNING" and row.last_error is None
    cursor_before = row.cursor
    # The run still advanced every other source.
    others = {s: r.status for s, r in result.items() if s != source}
    assert set(others.values()) == {"COMPLETE"}
    assert len(await stored(db_session, "WAF_TOP")) == len(world.expected["WAF_TOP"])

    resumed = await service(db_session).run()
    assert resumed.instances[name][source].status == "COMPLETE"
    assert resumed.instances[name][source].busy is False
    assert shape(await stored(db_session, source)) == shape(world.expected[source])
    done = (await states(db_session, instance_id))[source]
    assert done.cursor["until"] == cursor_before["until"]


@pytest.mark.parametrize(("source", "lock_name"), LOCKED)
async def test_the_collector_lock_is_held_during_a_batch_and_free_after_it(
    db_session, monkeypatch: pytest.MonkeyPatch, source: str, lock_name: str
) -> None:
    instance, _source, _world = await seed_world(db_session)
    instance_id = instance.id
    svc = service(db_session, entity_mention_backfill_batch_size=1)
    inside: list[bool] = []
    real = EntityMentionBackfillService._batch

    async def probing(self, iid, src, *args, **kwargs):  # type: ignore[no-untyped-def]
        if src == source:
            async with CollectorAdvisoryLock(
                db_session, settings(), instance_id, lock_name
            ) as taken:
                inside.append(taken)
        return await real(self, iid, src, *args, **kwargs)

    monkeypatch.setattr(EntityMentionBackfillService, "_batch", probing)
    for _ in range(40):  # one batch per run, so every run ends between two batches
        report = await svc.run(max_batches=1)
        async with CollectorAdvisoryLock(
            db_session, settings(), instance_id, lock_name
        ) as after:
            assert after is True
        if report.complete:
            break
    assert inside and not any(inside)  # held by the backfill while the batch ran


async def test_sources_without_a_live_replace_take_no_collector_lock(db_session) -> None:
    instance, _source, _world = await seed_world(db_session)
    instance_id = instance.id
    held = []
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        for lock_name in ("waf_endpoint_metric", "ips_collection"):
            held.append(
                await stack.enter_async_context(
                    CollectorAdvisoryLock(db_session, settings(), instance_id, lock_name)
                )
            )
        report = await service(db_session).run()
    assert held == [True, True]
    result = next(iter(report.instances.values()))
    assert result["WAF_TOP"].status == result["IPS_TOP"].status == "COMPLETE"
    assert not any(r.busy for r in result.values())


# ------------------------------------- D-66: committed buckets are never replaced
async def test_live_waf_collector_does_not_replace_a_bucket_at_the_watermark(
    db_session,
) -> None:
    """Pins the invariant that lets WAF_TOP backfill without a lock or a horizon."""
    settings_ = cfg(**OUTCOMES_ON)
    env = await waf_env(db_session, waf_provider(1), settings_)
    await waf_run(db_session, env, reset=False)
    stored_rows = await all_mentions(db_session)
    assert stored_rows
    instance_id = stored_rows[0].instance_id
    stale = EntityMentionRow(
        instance_id=instance_id, kind="IP", value_norm="203.0.113.250", role="SOURCE_IP",
        source="WAF_TOP", ref_table="waf_endpoint_metric", ref_id=stored_rows[0].ref_id,
        occurred_at=stored_rows[0].occurred_at,
    )
    await record_mentions(db_session, [stale])
    before = {mention_key(r) for r in await all_mentions(db_session)}

    await waf_run(db_session, env, reset=False)  # a later tick: only newer buckets

    assert {mention_key(r) for r in await all_mentions(db_session)} == before
    # The discriminating half: a watermark moved back DOES replace (that is what must
    # never happen to a bucket the backfill can read).
    await waf_run(db_session, env, reset=True)
    assert "203.0.113.250" not in {r.value_norm for r in await all_mentions(db_session)}


async def test_live_ips_collector_does_not_replace_a_bucket_at_the_watermark(
    db_session,
) -> None:
    from app.collectors.ips_collector import watermark_collector
    from app.models.monitoring import CollectionWatermark

    instance = await make_instance(db_session)
    _, source = await add_source(db_session, instance, 412)
    await ips_collect(db_session, instance)
    first = await all_mentions(db_session)
    assert first
    bucket = (await db_session.scalars(select(IpsSourceBucket))).one()
    await record_mentions(
        db_session,
        [
            EntityMentionRow(
                instance_id=instance.id, kind="IP", value_norm="203.0.113.250",
                role="SOURCE_IP", source="IPS_TOP", ref_table="ips_source_bucket",
                ref_id=str(source.id), occurred_at=bucket.bucket_start,
            )
        ],
    )
    before = {mention_key(r) for r in await all_mentions(db_session)}

    await ips_collect(db_session, instance)  # same clock: nothing after the watermark

    assert {mention_key(r) for r in await all_mentions(db_session)} == before
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.collector == watermark_collector(412)
        )
    )
    watermark.watermark_at -= timedelta(minutes=5)
    await db_session.flush()
    await ips_collect(db_session, instance)
    assert "203.0.113.250" not in {r.value_norm for r in await all_mentions(db_session)}


# ------------------------------------------------------------- operator CLI
NAME = "lab-console"


def _database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value


@pytest.fixture
def database() -> Iterator[str]:
    url = _database_url()
    engine = create_engine(url.replace("+asyncpg", "+psycopg"), future=True)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO qradar_instance (id, name, console_host, api_version, "
                    "verify_ssl, provider_kind, mcp_enabled, enabled, status, "
                    "consecutive_failures) VALUES (:id, :name, 'qradar.lab.local', '20.0', "
                    "true, 'REST', false, true, 'HEALTHY', 0)"
                ),
                {"id": uuid.uuid4(), "name": NAME},
            )
        yield url
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def operator(database: str, monkeypatch: pytest.MonkeyPatch) -> str:
    def maker() -> async_sessionmaker:  # type: ignore[type-arg]
        engine = create_async_engine(database, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(cli, "get_sessionmaker", maker)
    return database


def _sql(database: str, sql: str) -> list[tuple]:  # type: ignore[type-arg]
    engine = create_engine(database.replace("+asyncpg", "+psycopg"), future=True)
    try:
        with engine.connect() as connection:
            return [tuple(r) for r in connection.execute(text(sql))]
    finally:
        engine.dispose()


def test_cli_status_before_anything_ran(operator, capsys) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["status"]) == 0
    assert "has not started" in capsys.readouterr().out
    assert cli.main(["status", "--json"]) == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_cli_unknown_instance_exits_2_and_changes_nothing(operator, capsys) -> None:  # type: ignore[no-untyped-def]
    for argv in (["run", "--instance", "nope"], ["status", "--instance", "nope"]):
        assert cli.main(argv) == 2
        assert "nope" in capsys.readouterr().err
    assert _sql(operator, "SELECT count(*) FROM entity_mention_backfill") == [(0,)]


def test_cli_run_then_status_exit_0(operator, capsys) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["run", "--instance", NAME]) == 0
    out = capsys.readouterr().out
    for source in ("LOG_SOURCE", *SOURCES, "CATCHALL", "PW_VALUE"):
        assert f"{NAME} {source} COMPLETE" in out
    assert cli.main(["status", "--instance", NAME]) == 0
    status = capsys.readouterr().out.splitlines()
    assert len(status) == 8 and all("COMPLETE" in line for line in status)
    assert all("rows_written=0" in line and "completed=" in line for line in status)
    assert cli.main(["run", "--max-batches", "1"]) == 0  # nothing left to do
    assert "batches=0" in capsys.readouterr().out
    assert cli.main(["status", "--json"]) == 0


def test_cli_max_batches_stops_early_with_exit_0(operator, capsys) -> None:  # type: ignore[no-untyped-def]
    assert cli.main(["run", "--max-batches", "2"]) == 0
    rows = dict(_sql(operator, "SELECT source, status FROM entity_mention_backfill"))
    assert rows["EXPLANATION"] == rows["WAF_TOP"] == "COMPLETE"
    assert rows["IPS_TOP"] == "PENDING"
    capsys.readouterr()


def test_cli_run_exits_3_when_the_lock_is_held(operator, capsys) -> None:  # type: ignore[no-untyped-def]
    namespace = Settings().collection_advisory_lock_namespace
    key = collector_lock_key(LOCK_INSTANCE_ID, LOCK_NAME)
    engine = create_engine(operator.replace("+asyncpg", "+psycopg"), future=True)
    try:
        with engine.connect() as holder:
            holder.execute(
                text("SELECT pg_advisory_lock(:ns, :key)"), {"ns": namespace, "key": key}
            )
            try:
                assert cli.main(["run"]) == 3
                assert "holds the lock" in capsys.readouterr().out
                assert _sql(operator, "SELECT count(*) FROM entity_mention_backfill") == [(0,)]
            finally:
                holder.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)"), {"ns": namespace, "key": key}
                )
    finally:
        engine.dispose()
    assert cli.main(["run"]) == 0
    capsys.readouterr()


def test_cli_run_exits_4_when_a_source_fails(operator, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    async def boom(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret-value")

    real = EntityMentionBackfillService._waf_top
    monkeypatch.setattr(EntityMentionBackfillService, "_waf_top", boom)
    assert cli.main(["run"]) == 4
    out = capsys.readouterr().out
    assert f"{NAME} WAF_TOP FAILED" in out and "secret-value" not in out
    assert cli.main(["status"]) == 0
    status = capsys.readouterr().out
    assert "WAF_TOP FAILED" in status and "RuntimeError" in status
    assert "secret-value" not in status
    monkeypatch.setattr(EntityMentionBackfillService, "_waf_top", real)
    assert cli.main(["run"]) == 0  # the retry
    assert "WAF_TOP COMPLETE" in capsys.readouterr().out


def test_cli_exits_1_when_the_database_is_unreachable(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    url = make_url(_database_url()).set(port=1)

    def maker() -> async_sessionmaker:  # type: ignore[type-arg]
        engine = create_async_engine(url, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(cli, "get_sessionmaker", maker)
    for argv in (["status"], ["run"]):
        assert cli.main(argv) == 1
        assert "the database failed" in capsys.readouterr().err


def test_module_entry_point_prints_help() -> None:
    env = {**os.environ, "PYTHONPATH": ".", "ENCRYPTION_KEY": KEY}
    done = subprocess.run(
        [sys.executable, "-m", "app.cli.entity_mentions", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        timeout=60,
        check=False,
    )
    assert done.returncode == 0 and "Exit codes" in done.stdout
