"""SOC-16a2: the WAF and IPS collectors index what they store as entity mentions.

Mentions are a derived index of values the collectors already persist; the tests pin the exact
rows, the idempotent replace on re-collection, the constant statement cost, the failure
isolation and the retention that removes them with their parents.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.collectors.ips_collector import IpsCollector, watermark_collector
from app.models.entity import EntityMention
from app.models.enums import IpsDimension
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafEndpointMetric
from app.providers.mock import MockQRadarProvider
from app.services.entity_mentions import EntityMentionRow, record_mentions
from app.services.ips_retention import IpsRetention
from app.services.waf_retention import WafRetention
from tests.integration.factories import make_instance
from tests.integration.test_ips_collection import NOW, add_source
from tests.integration.test_ips_collection import cfg as ips_cfg
from tests.integration.test_ips_collection import counting as ips_counting
from tests.integration.test_waf_breakdown_collection import (
    CLIENTS,
    LOGIN,
    OUTCOMES_ON,
    BreakdownProvider,
    setup,
)
from tests.integration.test_waf_productionization import STEP, T0, batch, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def mentions(session, source: str | None = None) -> list[EntityMention]:  # type: ignore[no-untyped-def]
    query = select(EntityMention)
    if source is not None:
        query = query.where(EntityMention.source == source)
    return list(
        (
            await session.scalars(query.execution_options(populate_existing=True))
        ).all()
    )


def key(row: EntityMention) -> tuple[str, str, str, str, str, datetime]:
    return (row.value_norm, row.role, row.source, row.ref_table, row.ref_id, row.occurred_at)


# ======================================================================== WAF
def waf_provider(endpoints: int, clients: list[tuple[str, int]] | None = None):  # type: ignore[no-untyped-def]
    paths = [f"/svc/e{index}" for index in range(endpoints)]
    volume = [(path, "GET", 100) for path in paths]
    provider = BreakdownProvider([batch(T0, volume)])
    rows = [
        (path, "GET", ip, count)
        for path in paths
        for ip, count in (clients or [("198.51.100.1", 50), ("198.51.100.2", 30)])
    ]
    provider.serve("SOURCE_IP", T0, rows)
    return provider


async def waf_env(session, provider, settings):  # type: ignore[no-untyped-def]
    _profile, source, instance, _plan, collector = await setup(session, provider, settings)
    return instance, source, collector(T0 + STEP + timedelta(seconds=5))


async def waf_run(session, env, *, reset: bool):  # type: ignore[no-untyped-def]
    instance, source, run = env
    if reset:
        watermark = await session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == "waf_endpoint_metric:312"
            )
        )
        watermark.watermark_at = None
        await session.flush()
    await run.collect(instance, log_source=source)


async def waf_collect(session, provider, settings, *, reset: bool = False):  # type: ignore[no-untyped-def]
    env = await waf_env(session, provider, settings)
    await waf_run(session, env, reset=reset)
    return env[0], env[1]


async def test_waf_stored_top_clients_become_mentions_with_exact_values(db_session) -> None:
    settings = cfg(**OUTCOMES_ON)
    provider = BreakdownProvider([batch(T0, [(*LOGIN, 100)])])
    provider.serve("SOURCE_IP", T0, CLIENTS)
    instance, _ = await waf_collect(db_session, provider, settings, reset=False)

    endpoint = (await db_session.scalars(select(WafEndpoint))).one()
    stored = (await db_session.scalars(select(WafEndpointMetric))).one()
    assert [c["source_ip"] for c in stored.top_clients] == [
        "198.51.100.1", "198.51.100.2", "203.0.113.9"
    ]
    rows = await mentions(db_session)
    assert {key(row) for row in rows} == {
        (ip, "SOURCE_IP", "WAF_TOP", "waf_endpoint_metric", str(endpoint.id), T0)
        for ip in ("198.51.100.1", "198.51.100.2", "203.0.113.9")
    }
    assert {(row.instance_id, row.kind) for row in rows} == {(instance.id, "IP")}


async def test_waf_bucket_without_a_stored_client_list_records_nothing(db_session) -> None:
    # Client collection off: the metric stores no list, so nothing may leave the fold.
    settings = cfg()
    provider = BreakdownProvider([batch(T0, [(*LOGIN, 100)])])
    provider.serve("SOURCE_IP", T0, CLIENTS)
    await waf_collect(db_session, provider, settings, reset=False)
    assert (await db_session.scalars(select(WafEndpointMetric))).one().top_clients is None
    assert await mentions(db_session) == []


async def test_waf_unparsable_address_in_a_list_is_skipped_not_fatal(db_session) -> None:
    settings = cfg(**OUTCOMES_ON)
    provider = BreakdownProvider([batch(T0, [(*LOGIN, 100)])])
    provider.serve("SOURCE_IP", T0, CLIENTS)
    _, source, _, _, collector = await setup(db_session, provider, settings)
    waf_collector = collector(T0 + STEP)
    endpoint_id = uuid.uuid4()
    await waf_collector._record_client_mentions(
        source,
        T0,
        [
            {
                "waf_endpoint_id": endpoint_id,
                "top_clients": [
                    {"source_ip": "not-an-ip", "requests": 9},
                    {"source_ip": "", "requests": 8},
                    {"source_ip": " 198.51.100.7 ", "requests": 7},
                ],
            }
        ],
        replace_existing=False,
    )
    assert [row.value_norm for row in await mentions(db_session)] == ["198.51.100.7"]


async def test_waf_recollection_is_idempotent_and_replaces_a_changed_list(db_session) -> None:
    settings = cfg(**OUTCOMES_ON)
    provider = waf_provider(1)
    env = await waf_env(db_session, provider, settings)
    await waf_run(db_session, env, reset=False)
    first = {key(row) for row in await mentions(db_session)}
    assert len(first) == 2

    await waf_run(db_session, env, reset=True)
    assert {key(row) for row in await mentions(db_session)} == first

    provider.serve(
        "SOURCE_IP",
        T0,
        [("/svc/e0", "GET", "203.0.113.9", 60), ("/svc/e0", "GET", "198.51.100.1", 40)],
    )
    await waf_run(db_session, env, reset=True)
    assert {row.value_norm for row in await mentions(db_session)} == {
        "203.0.113.9", "198.51.100.1"
    }


async def test_waf_statement_cost_is_constant_per_bucket_at_two_endpoint_counts(
    db_session,
) -> None:
    settings = cfg(**OUTCOMES_ON)
    fresh: dict[int, list[str]] = {}
    again: dict[int, list[str]] = {}
    for endpoints in (2, 12):
        # Each size is its own source: a new session state per size would be heavier than needed.
        provider = waf_provider(endpoints)
        env = await waf_env(db_session, provider, settings)
        with ips_counting(db_session) as statements:
            await waf_run(db_session, env, reset=False)
        fresh[endpoints] = list(statements)
        with ips_counting(db_session) as statements:
            await waf_run(db_session, env, reset=True)
        again[endpoints] = list(statements)
        await db_session.execute(text("DELETE FROM entity_mention"))
        await db_session.execute(text("DELETE FROM waf_endpoint_metric"))
        await db_session.execute(text("DELETE FROM waf_source_bucket"))
        await db_session.execute(text("DELETE FROM waf_endpoint"))
        await db_session.execute(text("DELETE FROM collection_watermark"))
        await db_session.execute(text("DELETE FROM waf_source_profile"))
        await db_session.execute(text("DELETE FROM log_source"))
        await db_session.flush()

    def writer(statements: list[str]) -> list[str]:
        return [s for s in statements if "entity_mention" in s]

    # Fresh bucket: one INSERT. Re-collection: one DELETE and one INSERT. Never per endpoint.
    assert [s.split()[0] for s in writer(fresh[2])] == ["INSERT"]
    assert [s.split()[0] for s in writer(fresh[12])] == ["INSERT"]
    assert [s.split()[0] for s in writer(again[2])] == ["DELETE", "INSERT"]
    assert [s.split()[0] for s in writer(again[12])] == ["DELETE", "INSERT"]
    assert len(fresh[2]) == len(fresh[12])
    assert len(again[2]) == len(again[12])
    # Re-collection adds exactly the mention DELETE over a fresh bucket's shape.
    assert len(again[2]) - len(fresh[2]) == 1
    # Mentions add a savepoint pair and the INSERT: three statements on a fresh bucket.
    assert sum(s.startswith(("SAVEPOINT", "RELEASE SAVEPOINT")) for s in fresh[2]) == 2


async def test_waf_failure_to_record_mentions_keeps_the_metric_rows(
    db_session, monkeypatch, caplog
) -> None:
    async def failing(session, rows):  # type: ignore[no-untyped-def]
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr("app.collectors.waf_endpoint_collector.record_mentions", failing)
    settings = cfg(**OUTCOMES_ON)
    with caplog.at_level(logging.WARNING, logger="app.collectors.waf_endpoint_collector"):
        await waf_collect(db_session, waf_provider(1), settings, reset=False)
    assert len((await db_session.scalars(select(WafEndpointMetric))).all()) == 1
    assert await mentions(db_session) == []
    assert any(
        getattr(r, "error_class", None) == "DivisionByZeroError"
        or "ZeroDivision" in str(getattr(r, "error_class", ""))
        or "mentions were not recorded" in r.getMessage()
        for r in caplog.records
    )


# ======================================================================== IPS
async def ips_collect(session, instance, provider=None, **kw):  # type: ignore[no-untyped-def]
    provider = provider or MockQRadarProvider(now=NOW)
    await IpsCollector(session, provider, settings=ips_cfg(**kw), clock=lambda: NOW).collect(
        instance
    )


async def expected_ips(session) -> set[tuple[str, str, str, str, str, datetime]]:  # type: ignore[no-untyped-def]
    """The mentions the stored rows imply, derived from the stored rows only."""
    expected = set()
    for bucket in (await session.scalars(select(IpsSourceBucket))).all():
        for dimension in ("SOURCE_IP", "PORT_FANOUT"):
            for item in bucket.contributors.get(dimension, []):
                expected.add(
                    (item["source_ip"], "SOURCE_IP", "IPS_TOP", "ips_source_bucket",
                     str(bucket.log_source_id), bucket.bucket_start)
                )
    rows = (
        await session.execute(
            select(IpsEntity.id, IpsEntity.value_key, IpsEntityMetric.bucket_start)
            .join(IpsEntityMetric, IpsEntityMetric.ips_entity_id == IpsEntity.id)
            .where(IpsEntity.dimension == IpsDimension.DESTINATION_IP)
        )
    ).all()
    for entity_id, value, start in rows:
        expected.add((value, "DESTINATION_IP", "IPS_ENTITY", "ips_entity",
                      str(entity_id), start))
    return expected


async def test_ips_stored_contributors_become_mentions_with_exact_values(db_session) -> None:
    instance = await make_instance(db_session)
    await add_source(db_session, instance, 412)
    await ips_collect(db_session, instance)

    expected = await expected_ips(db_session)
    got = await mentions(db_session)
    assert {key(row) for row in got} == expected
    assert {row.source for row in got} == {"IPS_TOP", "IPS_ENTITY"}
    assert {row.role for row in got} == {"SOURCE_IP", "DESTINATION_IP"}
    assert {(row.instance_id, row.kind) for row in got} == {(instance.id, "IP")}


async def test_ips_nothing_is_recorded_when_no_contributor_is_stored(db_session) -> None:
    instance = await make_instance(db_session)
    _, source = await add_source(db_session, instance, 412)
    collector = IpsCollector(
        db_session, MockQRadarProvider(now=NOW), settings=ips_cfg(), clock=lambda: NOW
    )
    bucket = {"log_source_id": source.id, "bucket_start": NOW, "contributors": {}}
    with ips_counting(db_session) as statements:
        await collector._record_mentions(instance, [bucket], [], [(source.id, NOW)])
    assert await mentions(db_session) == []
    assert not [s for s in statements if s.startswith("INSERT")]
    # A source that is not due collects nothing and records nothing.
    empty = await make_instance(db_session)
    await ips_collect(db_session, empty)
    assert [row for row in await mentions(db_session) if row.instance_id == empty.id] == []


async def test_ips_unparsable_address_is_skipped_not_fatal(db_session) -> None:
    instance = await make_instance(db_session)
    _, source = await add_source(db_session, instance, 412)
    collector = IpsCollector(
        db_session, MockQRadarProvider(now=NOW), settings=ips_cfg(), clock=lambda: NOW
    )
    entity_id = uuid.uuid4()
    await collector._record_mentions(
        instance,
        [
            {
                "log_source_id": source.id,
                "bucket_start": NOW,
                "contributors": {
                    "SOURCE_IP": [{"source_ip": "bogus"}, {"source_ip": "192.0.2.5"}],
                    "PORT_FANOUT": [{"source_ip": None}],
                },
            }
        ],
        [(NOW, entity_id, "also-bogus")],
        [],
    )
    assert [(r.value_norm, r.source) for r in await mentions(db_session)] == [
        ("192.0.2.5", "IPS_TOP")
    ]


async def test_ips_port_fanout_members_are_source_ip_mentions(db_session) -> None:
    instance = await make_instance(db_session)
    _, source = await add_source(db_session, instance, 412)
    collector = IpsCollector(
        db_session, MockQRadarProvider(now=NOW), settings=ips_cfg(), clock=lambda: NOW
    )
    bucket = {
        "log_source_id": source.id,
        "bucket_start": NOW,
        "contributors": {"PORT_FANOUT": [{"source_ip": "192.0.2.77"}]},
    }
    await collector._record_mentions(instance, [bucket], [], [])
    assert [key(row) for row in await mentions(db_session)] == [
        ("192.0.2.77", "SOURCE_IP", "IPS_TOP", "ips_source_bucket", str(source.id), NOW)
    ]


async def test_ips_recollection_is_idempotent_and_replaces_a_changed_list(db_session) -> None:
    instance = await make_instance(db_session)
    _, source = await add_source(db_session, instance, 412)
    await ips_collect(db_session, instance)
    first = {key(row) for row in await mentions(db_session)}
    assert first

    async def reset() -> None:
        watermark = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == watermark_collector(412)
            )
        )
        watermark.watermark_at -= timedelta(minutes=5)
        await db_session.flush()

    await reset()
    await ips_collect(db_session, instance)
    assert {key(row) for row in await mentions(db_session)} == first == await expected_ips(
        db_session
    )

    # A stale mention for the same parent (a value no longer stored) is replaced away.
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
    entity = next(k for k in await expected_ips(db_session) if k[2] == "IPS_ENTITY")
    await record_mentions(
        db_session,
        [
            EntityMentionRow(
                instance_id=instance.id, kind="IP", value_norm="203.0.113.251",
                role="DESTINATION_IP", source="IPS_ENTITY", ref_table="ips_entity",
                ref_id=entity[4], occurred_at=entity[5],
            )
        ],
    )
    await reset()
    await ips_collect(db_session, instance)
    assert {key(row) for row in await mentions(db_session)} == await expected_ips(db_session)


async def test_ips_statement_cost_is_constant_at_two_fleet_sizes(db_session) -> None:
    counts: dict[int, list[str]] = {}
    for sources in (2, 12):
        instance = await make_instance(db_session)
        for index in range(sources):
            await add_source(db_session, instance, 700 + index)
        await db_session.flush()
        with ips_counting(db_session) as statements:
            await ips_collect(db_session, instance)
        counts[sources] = list(statements)

        def writer(rows: list[str]) -> list[str]:
            return [s.split()[0] for s in rows if "entity_mention" in s]

        # One DELETE (both sources) and one INSERT, whatever the fleet size.
        assert writer(counts[sources]) == ["DELETE", "INSERT"]
    assert len(counts[2]) == len(counts[12]) == 16

    # Re-collection of every bucket: the same two statements.
    watermarks = (await db_session.scalars(select(CollectionWatermark))).all()
    for watermark in watermarks:
        watermark.watermark_at -= timedelta(minutes=5)
    await db_session.flush()
    with ips_counting(db_session) as statements:
        await ips_collect(db_session, instance)
    assert [s.split()[0] for s in statements if "entity_mention" in s] == [
        "DELETE", "INSERT"
    ]


async def test_ips_failure_to_record_mentions_keeps_the_metric_rows(
    db_session, monkeypatch, caplog
) -> None:
    async def failing(session, rows):  # type: ignore[no-untyped-def]
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr("app.collectors.ips_collector.record_mentions", failing)
    instance = await make_instance(db_session)
    await add_source(db_session, instance, 412)
    with caplog.at_level(logging.WARNING):
        await ips_collect(db_session, instance)
    assert (await db_session.scalars(select(IpsSourceBucket))).one() is not None
    assert (await db_session.scalars(select(IpsEntityMetric))).all()
    assert await mentions(db_session) == []
    assert any("IPS entity mentions were not recorded" in r.getMessage() for r in caplog.records)


# ================================================================== retention
async def seed_mentions(session, instance, source: str, ref_table: str, when: datetime, tag: str):  # type: ignore[no-untyped-def]
    await record_mentions(
        session,
        [
            EntityMentionRow(
                instance_id=instance.id, kind="IP", value_norm=f"192.0.2.{n}",
                role="SOURCE_IP", source=source, ref_table=ref_table,
                ref_id=f"{tag}-{n}", occurred_at=when,
            )
            for n in range(1, 4)
        ],
    )


@pytest.mark.parametrize(
    ("source", "table", "service"),
    [
        ("WAF_TOP", "waf_endpoint_metric", "waf"),
        ("IPS_TOP", "ips_source_bucket", "ips"),
        ("IPS_ENTITY", "ips_entity", "ips"),
    ],
)
async def test_retention_removes_aged_mentions_and_nothing_newer(
    db_session, source: str, table: str, service: str
) -> None:
    now = datetime(2026, 10, 13, 10, 7, tzinfo=UTC)
    instance = await make_instance(db_session)
    await seed_mentions(db_session, instance, source, table, now - timedelta(days=40), "old")
    await seed_mentions(db_session, instance, source, table, now - timedelta(days=2), "new")
    # An unrelated source of the same age must survive: retention is scoped by `source`.
    await seed_mentions(db_session, instance, "EXPLANATION", "anomaly_explanation",
                        now - timedelta(days=40), "other")
    await db_session.commit()
    if service == "waf":
        settings = cfg(waf_retention_enabled=True, waf_metric_retention_days=35,
                       baseline_lookback_days=14, waf_retention_batch_size=100)
        report = await WafRetention(db_session, settings=settings, clock=lambda: now).run()
        deleted = report.deleted["entity_mention_waf_top"]
    else:
        settings = ips_cfg(ips_retention_enabled=True, ips_metric_retention_days=35,
                           ips_baseline_lookback_days=14, ips_retention_batch_size=100)
        report = await IpsRetention(db_session, settings=settings, clock=lambda: now).run()
        deleted = report.deleted[
            "entity_mention_ips_top" if source == "IPS_TOP" else "entity_mention_ips_entity"
        ]
    assert deleted == 3
    remaining = {(row.source, row.ref_id.split("-")[0]) for row in await mentions(db_session)}
    assert remaining == {(source, "new"), ("EXPLANATION", "other")}
