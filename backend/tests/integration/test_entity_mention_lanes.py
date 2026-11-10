"""SOC-16a3: explanation, offense and log-source writers index what they store as mentions.

The mentions are a derived index of values the collectors already persist. The tests pin the
exact rows (kind, role, source, parent key, `occurred_at`), the values that must not become
mentions, the idempotent replace on re-collection, the fixed statement cost at two sizes, the
failure isolation, the retention path of each parent and, per lane, that the real
`GET /api/v1/entities/timeline` route returns the item with a working `href`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from sqlalchemy import delete, func, select

from app.collectors.explanation_collector import ExplanationCollector
from app.collectors.offense_collector import OffenseCollector
from app.core.config import get_settings
from app.models.entity import EntityMention
from app.models.enums import AnomalyState, AnomalyType, EvidenceStatus, Severity
from app.models.explanation import AnomalyExplanationContributor
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from app.providers.dto import DimensionAggregate, DimensionValueCount, LogSourceDTO
from app.providers.mock import MockQRadarProvider
from app.services.entity_mentions import user_match_forms
from app.services.inventory_sync import InventorySyncService
from app.services.log_source_anomaly_retention import LogSourceAnomalyRetention
from app.services.snapshot_retention import SnapshotRetention
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_entity_mention_writers import (
    CLIENTS,
    LOGIN,
    OUTCOMES_ON,
    BreakdownProvider,
    batch,
    cfg,
    ips_collect,
    waf_collect,
)
from tests.integration.test_entity_timeline import (
    END,
    READER,
    START,
    T0,
    TimelineClient,
    _url,
    _watermark,
)
from tests.integration.test_fleet_api import run
from tests.integration.test_ips_collection import NOW as IPS_NOW
from tests.integration.test_ips_collection import add_source, counting
from tests.integration.test_offense_collection import NOW as OFFENSE_NOW
from tests.integration.test_offense_collection import StubProvider, offense
from tests.integration.test_waf_productionization import T0 as WAF_T0

pytestmark = [pytest.mark.integration]


async def mentions(session, source: str):  # type: ignore[no-untyped-def]
    return list(
        (
            await session.scalars(
                select(EntityMention)
                .where(EntityMention.source == source)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def shape(rows):  # type: ignore[no-untyped-def]
    return sorted((r.kind, r.value_norm, r.role, r.ref_table, r.ref_id) for r in rows)


def writes(statements: list[str]) -> list[str]:
    return [s for s in statements if "entity_mention" in s or "SAVEPOINT" in s]


async def noop(self, *args, **kwargs):  # type: ignore[no-untyped-def]
    return None


# ================================================================== explanation
class ScriptedProvider(MockQRadarProvider):
    """Returns the scripted values per dimension, in the anomaly and the baseline window."""

    def __init__(self, values: dict[str, list[str]], anomaly_start: datetime) -> None:
        super().__init__()
        self.values = values
        self.anomaly_start = anomaly_start

    async def get_dimension_aggregates(self, **kwargs):  # type: ignore[no-untyped-def]
        in_anomaly = kwargs["window_start"] >= self.anomaly_start
        out = []
        for dimension in kwargs["dimensions"]:
            values = self.values.get(dimension)
            if values is None:
                out.append(DimensionAggregate(dimension=dimension, available=False, error="x"))
                continue
            counts = [
                DimensionValueCount(value=v, count=(100 + index) * (3 if in_anomaly else 1))
                for index, v in enumerate(values)
            ]
            out.append(
                DimensionAggregate(
                    dimension=dimension,
                    values=counts,
                    distinct_count=len(counts),
                    total_count=sum(c.count for c in counts),
                    query="SCRIPTED",
                )
            )
        return out


EXPLAIN_VALUES = {
    "source_ip": ["203.0.113.50", "2001:DB8::1", "not-an-ip"],
    "destination_ip": ["10.0.0.5"],
    "username": ["CORP\\Alice", "bob@Example.com"],
    # Not named by the design: an address-shaped value here must not become a mention.
    "event_name": ["198.51.100.1"],
    "destination_port": ["443"],
}
START_A = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


def explain_settings(**extra: object):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(
        update={
            "explanation_dimensions": [*EXPLAIN_VALUES, "qid"],
            "explanation_top_values": 20,
            **extra,
        }
    )


def make_anomaly(source_id, *, start: datetime = START_A, **kw) -> LogSourceAnomaly:  # type: ignore[no-untyped-def]
    return LogSourceAnomaly(
        log_source_id=source_id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.HIGH,
        detected_at=start,
        anomaly_start=start,
        anomaly_end=start + timedelta(minutes=5),
        state=kw.pop("state", AnomalyState.OPEN),
        evidence_status=EvidenceStatus.PENDING,
        details={},
        **kw,
    )


async def explain(session, anomaly, values, *, settings=None):  # type: ignore[no-untyped-def]
    provider = ScriptedProvider(values, anomaly.anomaly_start)
    collector = ExplanationCollector(
        session, provider, settings=settings or explain_settings(), clock=lambda: START_A
    )
    return await collector.collect(anomaly)


async def explanation_env(session):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    log_source = await make_log_source(session, instance, name="edge-fw")
    anomaly = make_anomaly(log_source.id)
    session.add(anomaly)
    await session.flush()
    return instance, log_source, anomaly


@pytest.mark.asyncio
async def test_explanation_named_dimensions_become_mentions_with_exact_values(db_session) -> None:
    instance, _source, anomaly = await explanation_env(db_session)
    await explain(db_session, anomaly, EXPLAIN_VALUES)

    contributors = {
        (c.dimension, c.value): str(c.id)
        for c in await db_session.scalars(select(AnomalyExplanationContributor))
    }
    rows = await mentions(db_session, "EXPLANATION")
    ref = "anomaly_explanation_contributor"
    assert shape(rows) == sorted(
        [
            ("IP", "203.0.113.50", "SOURCE_IP", ref, contributors[("source_ip", "203.0.113.50")]),
            ("IP", "2001:db8::1", "SOURCE_IP", ref, contributors[("source_ip", "2001:DB8::1")]),
            ("IP", "10.0.0.5", "DESTINATION_IP", ref, contributors[("destination_ip", "10.0.0.5")]),
            ("USER", "alice", "USER", ref, contributors[("username", "CORP\\Alice")]),
            ("USER", "bob", "USER", ref, contributors[("username", "bob@Example.com")]),
        ]
    )
    # The timeline orders these by the package's anomaly window.
    assert {r.occurred_at for r in rows} == {START_A}
    assert {r.occurred_end for r in rows} == {START_A + timedelta(minutes=5)}
    assert {r.instance_id for r in rows} == {instance.id}
    # The domain-qualified samples stay in step with the read side's match forms.
    stored = {r.value_norm for r in rows if r.kind == "USER"}
    # One bare-account row per stored username, the form every query spelling binds.
    assert stored == {user_match_forms("CORP\\Alice")[-1], user_match_forms("bob@Example.com")[-1]}
    assert stored == {"alice", "bob"}


@pytest.mark.asyncio
async def test_explanation_other_dimensions_and_unparsable_values_record_nothing(
    db_session,
) -> None:
    _instance, _source, anomaly = await explanation_env(db_session)
    report = await explain(db_session, anomaly, EXPLAIN_VALUES)

    assert report.status in (EvidenceStatus.COMPLETE, EvidenceStatus.PARTIAL)
    stored = {
        (c.dimension, c.value)
        for c in await db_session.scalars(select(AnomalyExplanationContributor))
    }
    # All contributors are stored; the unparsable address and the address-shaped event
    # name are among them but not among the mentions.
    assert ("source_ip", "not-an-ip") in stored and ("event_name", "198.51.100.1") in stored
    values = {r.value_norm for r in await mentions(db_session, "EXPLANATION")}
    assert "not-an-ip" not in values and "198.51.100.1" not in values and "443" not in values


@pytest.mark.asyncio
async def test_explanation_recollection_replaces_a_changed_list(db_session) -> None:
    _instance, _source, anomaly = await explanation_env(db_session)
    await explain(db_session, anomaly, EXPLAIN_VALUES)
    before = len(await mentions(db_session, "EXPLANATION"))
    assert before == 5

    await explain(db_session, anomaly, EXPLAIN_VALUES)
    assert len(await mentions(db_session, "EXPLANATION")) == before  # no duplicates

    await explain(db_session, anomaly, {"source_ip": ["198.51.100.9"], "username": ["carol"]})
    rows = await mentions(db_session, "EXPLANATION")
    assert [(r.kind, r.value_norm) for r in sorted(rows, key=lambda r: r.value_norm)] == [
        ("IP", "198.51.100.9"),
        ("USER", "carol"),
    ]
    live = {str(c.id) for c in await db_session.scalars(select(AnomalyExplanationContributor))}
    assert {r.ref_id for r in rows} <= live  # none points at a replaced contributor


@pytest.mark.asyncio
async def test_explanation_failed_collection_leaves_no_stale_mention(db_session) -> None:
    class Failing(ScriptedProvider):
        async def get_dimension_aggregates(self, **kwargs):  # type: ignore[no-untyped-def]
            raise ValueError("boom")

    _instance, _source, anomaly = await explanation_env(db_session)
    await explain(db_session, anomaly, EXPLAIN_VALUES)
    assert await mentions(db_session, "EXPLANATION")
    report = await ExplanationCollector(
        db_session,
        Failing({}, anomaly.anomaly_start),
        settings=explain_settings(),
        clock=lambda: START_A,
    ).collect(anomaly)
    assert report.status is EvidenceStatus.FAILED
    assert await mentions(db_session, "EXPLANATION") == []


@pytest.mark.asyncio
async def test_explanation_statement_cost_is_fixed_at_two_sizes(db_session, monkeypatch) -> None:
    async def delta(values: dict[str, list[str]], recollect: bool) -> int:
        instance = await make_instance(db_session)
        log_source = await make_log_source(db_session, instance, name="s")
        anomaly = make_anomaly(log_source.id)
        anomaly2 = make_anomaly(log_source.id, start=START_A + timedelta(days=1))
        db_session.add_all([anomaly, anomaly2])
        await db_session.flush()
        if recollect:
            await explain(db_session, anomaly, values)
            await explain(db_session, anomaly2, values)
        with counting(db_session) as with_writer:
            await explain(db_session, anomaly, values)
        with monkeypatch.context() as patch:
            patch.setattr(ExplanationCollector, "_record_mentions", noop)
            patch.setattr(ExplanationCollector, "_drop_mentions", noop)
            with counting(db_session) as without:
                await explain(db_session, anomaly2, values)
        assert not [s for s in without if "entity_mention" in s]
        return len(with_writer) - len(without)

    small = {"source_ip": ["203.0.113.1", "203.0.113.2"]}
    large = {
        "source_ip": [f"203.0.113.{n}" for n in range(1, 15)],
        "destination_ip": [f"10.0.0.{n}" for n in range(1, 15)],
        "username": [f"user{n}" for n in range(14)],
    }
    # Fresh package: SAVEPOINT, INSERT, RELEASE. Re-collection adds the stale-mention
    # DELETE inside its own savepoint: three more.
    assert await delta(small, False) == 3
    assert await delta(large, False) == 3
    assert await delta(small, True) == 6
    assert await delta(large, True) == 6


@pytest.mark.asyncio
async def test_explanation_mention_failure_keeps_the_package(
    db_session, monkeypatch, caplog
) -> None:
    async def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("203.0.113.50 secret-value")

    monkeypatch.setattr("app.collectors.explanation_collector.record_mentions", boom)
    _instance, _source, anomaly = await explanation_env(db_session)
    with caplog.at_level(logging.WARNING, logger="app.collectors.explanation"):
        report = await explain(db_session, anomaly, EXPLAIN_VALUES)

    assert report.contributors_written > 0
    assert await db_session.scalar(
        select(func.count()).select_from(AnomalyExplanationContributor)
    )
    assert await mentions(db_session, "EXPLANATION") == []
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "RuntimeError" in text and "secret-value" not in text and "203.0.113.50" not in text


@pytest.mark.asyncio
async def test_explanation_mentions_leave_with_their_incident_and_only_then(db_session) -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    days = 10
    old = now - timedelta(days=days, seconds=1) - timedelta(hours=1)
    settings = get_settings().model_copy(
        update={
            "log_source_anomaly_retention_days": days,
            "log_source_anomaly_retention_batch_size": 100,
            "log_source_anomaly_retention_max_batches": 20,
            "retention_enabled": False,
        }
    )
    instance = await make_instance(db_session)
    log_source = await make_log_source(db_session, instance, name="edge-fw")
    gone = make_anomaly(log_source.id, start=old, state=AnomalyState.RESOLVED, resolved_at=old)
    kept = make_anomaly(log_source.id, start=old, state=AnomalyState.OPEN)
    db_session.add_all([gone, kept])
    await db_session.flush()
    gone_id = gone.id
    values = {"source_ip": ["203.0.113.1", "203.0.113.2"], "username": ["dave"]}
    await explain(db_session, gone, values)
    await explain(db_session, kept, values)
    await db_session.commit()
    assert len(await mentions(db_session, "EXPLANATION")) == 6

    with counting(db_session) as statements:
        report = await LogSourceAnomalyRetention(
            db_session, settings=settings, clock=lambda: now
        ).run()

    assert report.deleted["entity_mention_explanation"] == 3
    assert report.incomplete == []
    rows = await mentions(db_session, "EXPLANATION")
    assert len(rows) == 3
    survivors = {
        str(c.id)
        for c in await db_session.scalars(
            select(AnomalyExplanationContributor).execution_options(populate_existing=True)
        )
    }
    assert {r.ref_id for r in rows} == survivors  # the open incident's own mentions
    assert not await db_session.scalar(
        select(func.count()).select_from(LogSourceAnomaly).where(LogSourceAnomaly.id == gone_id)
    )
    # One bounded DELETE for the mentions, however many there were.
    assert sum("DELETE FROM entity_mention" in s for s in statements) == 1


def test_explanation_end_to_end_through_the_timeline_route(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        log_source = await make_log_source(session, instance, name="edge-fw")
        anomaly = make_anomaly(log_source.id, start=T0)
        session.add(anomaly)
        await session.flush()
        await explain(session, anomaly, {"source_ip": ["203.0.113.50"], "username": ["Dave"]})
        return instance.id, anomaly.id

    instance_id, anomaly_id = run(seed)
    client = TimelineClient(READER)
    body = client.get(_url(instance_id, value="203.0.113.50")).json()
    items = [i for i in body["items"] if i["source"] == "EXPLANATION"]
    assert len(items) == 1
    assert items[0]["href"] == f"/anomalies/{anomaly_id}"
    assert items[0]["role"] == "SOURCE_IP"
    assert body["dangling"] == 0
    # A domain-qualified query reaches the stored account form too.
    user = client.get(_url(instance_id, value="DOM\\dave", kind="user")).json()
    assert [i["source"] for i in user["items"]] == ["EXPLANATION"]


# ===================================================================== offenses
async def collect_offenses(session, instance, dtos, *, clock=None):  # type: ignore[no-untyped-def]
    collector = OffenseCollector(
        session,
        StubProvider([list(dtos)]),
        settings=get_settings(),
        clock=clock or (lambda: OFFENSE_NOW),
    )
    return await collector.collect(instance)


def offense_shape(rows):  # type: ignore[no-untyped-def]
    return sorted((r.kind, r.value_norm, r.role, r.ref_id) for r in rows)


@pytest.mark.asyncio
async def test_offense_usernames_and_ip_source_become_mentions_with_exact_values(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    start = OFFENSE_NOW - timedelta(hours=4)
    updated = OFFENSE_NOW - timedelta(minutes=5)
    await collect_offenses(
        db_session,
        instance,
        [
            offense(
                4242,
                usernames=["CORP\\Alice", "bob@Example.com", "svc_backup", "   "],
                offense_source="10.20.30.41",
                offense_type=0,
                source_addresses=["12", "203.0.113.9"],
                local_destination_addresses=["77", "10.9.9.9"],
                start_time=start,
                last_updated_time=updated,
            )
        ],
    )
    rows = await mentions(db_session, "OFFENSE")
    assert offense_shape(rows) == sorted(
        [
            ("USER", "alice", "USER", "4242"),
            ("USER", "bob", "USER", "4242"),
            ("USER", "svc_backup", "USER", "4242"),
            ("IP", "10.20.30.41", "SOURCE_IP", "4242"),
        ]
    )
    assert {r.ref_table for r in rows} == {"offense_snapshot"}
    assert {r.occurred_at for r in rows} == {start}
    assert {r.occurred_end for r in rows} == {updated}
    # Address-id lists never become mentions, whatever they look like.
    assert not {"12", "77", "203.0.113.9", "10.9.9.9"} & {r.value_norm for r in rows}
    stored = {r.value_norm for r in rows}
    assert {"alice", "bob"} <= stored and "corp\\alice" not in stored


@pytest.mark.asyncio
async def test_offense_destination_type_and_unclassifiable_source(db_session) -> None:
    instance = await make_instance(db_session)
    await collect_offenses(
        db_session,
        instance,
        [
            offense(1, offense_source="198.51.100.7", offense_type=1, usernames=[]),
            offense(2, offense_source="web-01.example.com", offense_type=7, usernames=[]),
            offense(3, offense_source="alice", offense_type=3, usernames=[]),
            offense(4, offense_source=None, usernames=[]),
        ],
    )
    rows = await mentions(db_session, "OFFENSE")
    # Text the helper cannot classify (a host name, a bare account) records nothing.
    assert offense_shape(rows) == [("IP", "198.51.100.7", "DESTINATION_IP", "1")]
    assert await db_session.scalar(select(func.count()).select_from(OffenseSnapshot)) == 4


@pytest.mark.asyncio
async def test_offense_without_start_time_uses_the_last_update(db_session) -> None:
    instance = await make_instance(db_session)
    updated = OFFENSE_NOW - timedelta(minutes=5)
    await collect_offenses(
        db_session,
        instance,
        [offense(5, start_time=None, last_updated_time=updated, usernames=["erin"])],
    )
    (row,) = await mentions(db_session, "OFFENSE")
    assert row.occurred_at == updated and row.occurred_end is None


@pytest.mark.asyncio
async def test_offense_recollection_is_idempotent_and_replaces_a_changed_list(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    first = offense(7, usernames=["alice", "bob"], offense_source="10.0.0.1")
    await collect_offenses(db_session, instance, [first])
    assert len(await mentions(db_session, "OFFENSE")) == 3

    # Unchanged offense: no new snapshot, the same mentions.
    await collect_offenses(
        db_session, instance, [first], clock=lambda: OFFENSE_NOW + timedelta(minutes=5)
    )
    assert len(await mentions(db_session, "OFFENSE")) == 3

    # The username list and the source changed: the earlier mentions go.
    changed = offense(7, usernames=["bob", "carol"], offense_source="10.0.0.2")
    await collect_offenses(
        db_session, instance, [changed], clock=lambda: OFFENSE_NOW + timedelta(minutes=10)
    )
    assert offense_shape(await mentions(db_session, "OFFENSE")) == sorted(
        [
            ("USER", "bob", "USER", "7"),
            ("USER", "carol", "USER", "7"),
            ("IP", "10.0.0.2", "SOURCE_IP", "7"),
        ]
    )
    # Another offense's mentions are untouched by a replacement.
    await collect_offenses(
        db_session,
        instance,
        [offense(8, usernames=["dora"], offense_source=None)],
        clock=lambda: OFFENSE_NOW + timedelta(minutes=15),
    )
    await collect_offenses(
        db_session,
        instance,
        [offense(7, usernames=["bob"], offense_source=None)],
        clock=lambda: OFFENSE_NOW + timedelta(minutes=20),
    )
    assert offense_shape(await mentions(db_session, "OFFENSE")) == sorted(
        [("USER", "bob", "USER", "7"), ("USER", "dora", "USER", "8")]
    )


@pytest.mark.asyncio
async def test_offense_statement_cost_is_fixed_at_two_sizes(db_session, monkeypatch) -> None:
    async def run_once(count: int, patched: bool) -> list[str]:
        instance = await make_instance(db_session)
        dtos = [
            offense(
                n,
                usernames=[f"u{n}", f"v{n}"],
                offense_source=f"10.0.{n // 250}.{n % 250 + 1}",
            )
            for n in range(1, count + 1)
        ]
        with monkeypatch.context() as patch:
            if patched:
                patch.setattr(OffenseCollector, "_record_mentions", noop)
            with counting(db_session) as statements:
                await collect_offenses(db_session, instance, dtos)
        return statements

    deltas = []
    for count in (3, 12):
        with_writer = await run_once(count, False)
        without = await run_once(count, True)
        deltas.append(len(with_writer) - len(without))
        assert not [s for s in without if "entity_mention" in s]
    # SAVEPOINT, one DELETE for every offense stored, one INSERT, RELEASE.
    assert deltas == [4, 4]


@pytest.mark.asyncio
async def test_offense_run_that_stores_nothing_writes_no_mention_statement(db_session) -> None:
    instance = await make_instance(db_session)
    dto = offense(9, usernames=["alice"])
    await collect_offenses(db_session, instance, [dto])
    with counting(db_session) as statements:
        await collect_offenses(
            db_session, instance, [dto], clock=lambda: OFFENSE_NOW + timedelta(minutes=5)
        )
    assert writes(statements) == []


@pytest.mark.asyncio
async def test_offense_mention_failure_keeps_the_snapshots(
    db_session, monkeypatch, caplog
) -> None:
    async def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("alice 10.20.30.41")

    monkeypatch.setattr("app.collectors.offense_collector.record_mentions", boom)
    instance = await make_instance(db_session)
    with caplog.at_level(logging.WARNING, logger="app.collectors.offense"):
        report = await collect_offenses(
            db_session,
            instance,
            [offense(10, usernames=["alice"], offense_source="10.20.30.41")],
        )
    assert report.snapshots_written == 1 and not report.partial_failure
    assert await db_session.scalar(select(func.count()).select_from(OffenseSnapshot)) == 1
    assert await mentions(db_session, "OFFENSE") == []
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "RuntimeError" in text and "alice" not in text and "10.20.30.41" not in text


@pytest.mark.asyncio
async def test_offense_mentions_leave_when_the_policy_has_dropped_every_snapshot(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await collect_offenses(
        db_session,
        instance,
        [
            offense(21, usernames=["gone1", "gone2"], offense_source="10.0.0.21"),
            offense(22, usernames=["kept1", "kept2"], offense_source="10.0.0.22"),
        ],
    )
    await db_session.commit()
    # The Timescale policy drops chunks outside this code; model it by deleting one
    # offense's snapshots.
    await db_session.execute(
        delete(OffenseSnapshot).where(OffenseSnapshot.qradar_offense_id == 21)
    )
    await db_session.commit()
    now = OFFENSE_NOW + timedelta(days=40)
    enabled = get_settings().model_copy(
        update={"retention_enabled": True, "retention_offense_snapshot_days": 30}
    )
    off = get_settings().model_copy(update={"retention_enabled": False})

    report = await SnapshotRetention(db_session, settings=off, clock=lambda: now).run()
    assert "entity_mention_offense" not in report.deleted  # the policy is off: no scan
    assert len(await mentions(db_session, "OFFENSE")) == 6

    with counting(db_session) as statements:
        report = await SnapshotRetention(db_session, settings=enabled, clock=lambda: now).run()

    assert report.deleted["entity_mention_offense"] == 3
    assert sorted({r.ref_id for r in await mentions(db_session, "OFFENSE")}) == ["22"]
    assert sum("DELETE FROM entity_mention" in s for s in statements) == 1


def test_offense_end_to_end_through_the_timeline_route(db_schema) -> None:
    now = datetime.now(UTC)

    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add(_watermark(instance.id))
        await session.flush()
        await collect_offenses(
            session,
            instance,
            [
                offense(
                    7001,
                    usernames=["CORP\\Alice", "svc_backup"],
                    offense_source="192.0.2.44",
                    start_time=now - timedelta(hours=1),
                    last_updated_time=now - timedelta(minutes=5),
                )
            ],
            clock=lambda: now,
        )
        return instance.id

    instance_id = run(seed)
    window = {
        "start": (now - timedelta(hours=3)).isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
    }
    client = TimelineClient(READER)
    # A domain-qualified query reaches a bare stored account through its match forms.
    for value, kind in (
        ("192.0.2.44", "ip"),
        ("alice", "user"),
        ("CORP\\Alice", "user"),
        ("OTHER\\svc_backup", "user"),
    ):
        body = client.get(_url(instance_id, value=value, kind=kind, **window)).json()
        items = [i for i in body["items"] if i["source"] == "OFFENSE"]
        assert len(items) == 1, (value, body)
        assert items[0]["href"] == f"/offenses/7001?instance_id={instance_id}"
        assert body["dangling"] == 0


# ================================================================== log sources
def source_dto(qradar_id: int, name: str, identifier: str | None) -> LogSourceDTO:
    return LogSourceDTO(qradar_id=qradar_id, name=name, identifier=identifier, type_id=11)


class InventoryProvider(MockQRadarProvider):
    def __init__(self, dtos: list[LogSourceDTO]) -> None:
        super().__init__()
        self.dtos = dtos

    async def list_log_sources(self):  # type: ignore[no-untyped-def]
        return list(self.dtos)


async def sync(session, instance, dtos, at):  # type: ignore[no-untyped-def]
    service = InventorySyncService(session, InventoryProvider(dtos), clock=lambda: at)
    return await service.sync(instance)


SYNC_1 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
SYNC_2 = SYNC_1 + timedelta(hours=1)


@pytest.mark.asyncio
async def test_log_source_name_and_identifier_become_mentions_with_exact_values(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await sync(
        db_session,
        instance,
        [
            source_dto(1, "Edge-FW.", "192.0.2.44"),
            source_dto(2, "proxy-01", "Proxy-01.Example.com."),
            source_dto(3, "name-only", None),
            source_dto(4, "v6-fw", "2001:DB8::2"),
        ],
        SYNC_1,
    )
    ids = {s.qradar_id: str(s.id) for s in await db_session.scalars(select(LogSource))}
    rows = await mentions(db_session, "LOG_SOURCE")
    assert shape(rows) == sorted(
        [
            ("HOST", "edge-fw", "HOST", "log_source", ids[1]),
            ("HOST", "192.0.2.44", "HOST", "log_source", ids[1]),
            ("IP", "192.0.2.44", "HOST", "log_source", ids[1]),
            ("HOST", "proxy-01", "HOST", "log_source", ids[2]),
            ("HOST", "proxy-01.example.com", "HOST", "log_source", ids[2]),
            ("HOST", "name-only", "HOST", "log_source", ids[3]),
            ("HOST", "v6-fw", "HOST", "log_source", ids[4]),
            ("HOST", "2001:db8::2", "HOST", "log_source", ids[4]),
            ("IP", "2001:db8::2", "HOST", "log_source", ids[4]),
        ]
    )
    # An identity: dated by the observation that confirmed it.
    assert {r.occurred_at for r in rows} == {SYNC_1}
    assert {r.occurred_end for r in rows} == {None}


@pytest.mark.asyncio
async def test_log_source_resync_is_idempotent_and_follows_renames_and_removals(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    dtos = [source_dto(1, "edge-fw", "192.0.2.44"), source_dto(2, "old-name", "10.0.0.2")]
    await sync(db_session, instance, dtos, SYNC_1)
    first = len(await mentions(db_session, "LOG_SOURCE"))
    assert first == 6

    await sync(db_session, instance, dtos, SYNC_2)
    rows = await mentions(db_session, "LOG_SOURCE")
    assert len(rows) == first  # no new mention on a re-sync
    assert {r.occurred_at for r in rows} == {SYNC_2}

    renamed = [source_dto(1, "edge-fw", "192.0.2.44"), source_dto(2, "new-name", "10.0.0.2")]
    await sync(db_session, instance, renamed, SYNC_2 + timedelta(hours=1))
    values = {r.value_norm for r in await mentions(db_session, "LOG_SOURCE")}
    assert "new-name" in values and "old-name" not in values

    # A source the console no longer lists keeps its row but no inventory mention.
    await sync(db_session, instance, renamed[:1], SYNC_2 + timedelta(hours=2))
    values = {r.value_norm for r in await mentions(db_session, "LOG_SOURCE")}
    assert values == {"edge-fw", "192.0.2.44"}
    assert await db_session.scalar(select(func.count()).select_from(LogSource)) == 2


@pytest.mark.asyncio
async def test_log_source_mentions_are_scoped_to_their_instance(db_session) -> None:
    one = await make_instance(db_session)
    other = await make_instance(db_session)
    await sync(db_session, one, [source_dto(1, "shared-name", "10.1.1.1")], SYNC_1)
    await sync(db_session, other, [source_dto(1, "other-name", "10.2.2.2")], SYNC_1)
    await sync(db_session, one, [source_dto(1, "shared-name", "10.1.1.1")], SYNC_2)
    by_instance = {
        r.instance_id: r.value_norm
        for r in await mentions(db_session, "LOG_SOURCE")
        if r.kind == "IP"
    }
    assert by_instance == {one.id: "10.1.1.1", other.id: "10.2.2.2"}


@pytest.mark.asyncio
async def test_log_source_statement_cost_is_fixed_at_two_sizes(db_session, monkeypatch) -> None:
    async def run_once(count: int, patched: bool) -> list[str]:
        instance = await make_instance(db_session)
        dtos = [
            source_dto(n, f"src-{n}", f"10.0.{n // 250}.{n % 250 + 1}")
            for n in range(1, count + 1)
        ]
        with monkeypatch.context() as patch:
            if patched:
                patch.setattr(InventorySyncService, "_record_mentions", noop)
            with counting(db_session) as statements:
                await sync(db_session, instance, dtos, SYNC_1)
        return statements

    deltas = []
    for count in (3, 40):
        with_writer = await run_once(count, False)
        without = await run_once(count, True)
        deltas.append(len(with_writer) - len(without))
        assert not [s for s in without if "entity_mention" in s]
    # SAVEPOINT, one DELETE, one INSERT (up to 1,000 rows), RELEASE.
    assert deltas == [4, 4]


@pytest.mark.asyncio
async def test_log_source_mention_failure_keeps_the_inventory(
    db_session, monkeypatch, caplog
) -> None:
    async def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("edge-fw 192.0.2.44")

    monkeypatch.setattr("app.services.inventory_sync.record_mentions", boom)
    instance = await make_instance(db_session)
    with caplog.at_level(logging.WARNING, logger="app.services.inventory_sync"):
        result = await sync(
            db_session, instance, [source_dto(1, "edge-fw", "192.0.2.44")], SYNC_1
        )
    assert result.created == 1
    assert await db_session.scalar(select(func.count()).select_from(LogSource)) == 1
    assert await mentions(db_session, "LOG_SOURCE") == []
    text = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "RuntimeError" in text and "edge-fw" not in text and "192.0.2.44" not in text


@pytest.mark.asyncio
async def test_log_source_mentions_go_with_the_instance(db_session) -> None:
    # No code deletes a log source row: the inventory pass drops what the console stopped
    # listing, and deleting the console cascades the rest.
    instance = await make_instance(db_session)
    await sync(db_session, instance, [source_dto(1, "edge-fw", "192.0.2.44")], SYNC_1)
    await db_session.commit()
    assert await mentions(db_session, "LOG_SOURCE")

    await db_session.execute(delete(QRadarInstance).where(QRadarInstance.id == instance.id))
    await db_session.commit()
    assert await mentions(db_session, "LOG_SOURCE") == []


def test_log_source_end_to_end_through_the_timeline_route(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        await sync(session, instance, [source_dto(1, "Edge FW.", "192.0.2.44")], T0)
        return instance.id

    instance_id = run(seed)
    client = TimelineClient(READER)
    window = {"start": START.isoformat(), "end": END.isoformat()}
    for value, kind in (("192.0.2.44", "ip"), ("192.0.2.44", "host"), ("edge fw", "host")):
        body = client.get(_url(instance_id, value=value, kind=kind, **window)).json()
        items = [i for i in body["items"] if i["source"] == "LOG_SOURCE"]
        assert len(items) == 1, (value, kind, body)
        assert items[0]["href"] == f"/fleet/sources?q={quote('Edge FW.', safe='')}"
        assert body["dangling"] == 0


# ============================================== SOC-16a2 lanes through the real route
def _window(at: datetime) -> dict[str, str]:
    return {
        "start": (at - timedelta(hours=1)).isoformat(),
        "end": (at + timedelta(hours=1)).isoformat(),
    }


def test_waf_top_clients_end_to_end_through_the_timeline_route(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        provider = BreakdownProvider([batch(WAF_T0, [(*LOGIN, 100)])])
        provider.serve("SOURCE_IP", WAF_T0, CLIENTS)
        instance, _source = await waf_collect(session, provider, cfg(**OUTCOMES_ON))
        return instance.id

    instance_id = run(seed)
    body = TimelineClient(READER).get(
        _url(instance_id, value="198.51.100.1", **_window(WAF_T0))
    ).json()
    items = [i for i in body["items"] if i["source"] == "WAF_TOP"]
    assert len(items) == 1, body
    assert items[0]["href"] and body["dangling"] == 0


def test_ips_lanes_end_to_end_through_the_timeline_route(db_schema) -> None:
    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        await add_source(session, instance, 412)
        await ips_collect(session, instance)
        top = await session.scalar(
            select(EntityMention).where(EntityMention.source == "IPS_TOP").limit(1)
        )
        entity = await session.scalar(
            select(EntityMention).where(EntityMention.source == "IPS_ENTITY").limit(1)
        )
        assert top is not None and entity is not None
        return instance.id, top.value_norm, entity.value_norm

    instance_id, top_ip, destination_ip = run(seed)
    client = TimelineClient(READER)
    for lane, value in (("IPS_TOP", top_ip), ("IPS_ENTITY", destination_ip)):
        body = client.get(_url(instance_id, value=value, **_window(IPS_NOW))).json()
        items = [i for i in body["items"] if i["source"] == lane]
        assert items, (lane, body)
        assert all(i["href"] for i in items) and body["dangling"] == 0, (lane, body)


# ================================== both username directions, end to end
def test_username_forms_find_each_other_through_the_timeline_route(db_schema) -> None:
    now = datetime.now(UTC)

    async def seed(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        session.add(_watermark(instance.id))
        log_source = await make_log_source(session, instance, name="edge-fw")
        anomaly = make_anomaly(log_source.id, start=now - timedelta(hours=1))
        session.add(anomaly)
        await session.flush()
        await explain(session, anomaly, {"username": ["CORP\\Alice", "bob"]})
        await collect_offenses(
            session,
            instance,
            [
                offense(
                    7002,
                    usernames=["CORP\\Carol", "dave"],
                    offense_source=None,
                    start_time=now - timedelta(hours=1),
                    last_updated_time=now - timedelta(minutes=5),
                )
            ],
            clock=lambda: now,
        )
        return instance.id

    instance_id = run(seed)
    window = _window(now)
    client = TimelineClient(READER)
    cases = (
        ("alice", "EXPLANATION"),  # bare query finds a stored qualified name
        ("CORP\\Alice", "EXPLANATION"),
        ("OTHER\\bob", "EXPLANATION"),  # qualified query finds a stored bare name
        ("carol", "OFFENSE"),
        ("CORP\\Carol", "OFFENSE"),
        ("OTHER\\dave", "OFFENSE"),
    )
    for value, lane in cases:
        body = client.get(_url(instance_id, value=value, kind="user", **window)).json()
        items = [i for i in body["items"] if i["source"] == lane]
        assert len(items) == 1, (value, lane, body)
        assert body["dangling"] == 0
