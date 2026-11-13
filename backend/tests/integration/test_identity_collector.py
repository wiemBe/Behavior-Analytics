"""Identity collection against the mock: buckets, failures, gaps, lock, SQL cost."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy import text as sql_text

from app.core.config import Settings
from app.identity.collector import COLLECTOR_NAME, IdentityCollector
from app.identity.dto import IdentitySearchKind
from app.models.enums import (
    IDENTITY_EVENT_CATALOG,
    IdentityMeasurement,
    IdentityProfileValidationStatus,
)
from app.models.identity_detection import (
    IdentityBucket,
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider
from app.services.locks import collector_lock_key
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
WIDTH = timedelta(seconds=300)
CATALOG = [event_id for event_id, _kind in IDENTITY_EVENT_CATALOG]
EXACT = IdentityMeasurement.EXACT.value
TRUNCATED = IdentityMeasurement.TRUNCATED.value
MISSING = IdentityMeasurement.NOT_COLLECTED.value
VALID = IdentityProfileValidationStatus.VALID.value


def _settings(**over: object) -> Settings:
    values: dict[str, object] = {
        "search_cadence_settle_seconds": 0,
        "collection_interval_seconds": 300,
        "collection_max_backfill_intervals": 0,
        "identity_max_sources_per_search": 50,
        "ariel_multi_bucket_max_rows": 5000,
        "search_cadence_max_searches_per_tick": 16,
    }
    values.update(over)
    return Settings(**values)  # type: ignore[arg-type]


async def _profile(
    session,
    instance: QRadarInstance,
    source: LogSource,
    *,
    enabled: bool = True,
    status: str = VALID,
    version: int = 1,
    validated: int | None = 1,
    event_id_property: str = "EventID",
    username_property: str = "username",
    event_ids: list[int] | None = None,
) -> IdentitySourceProfile:
    row = IdentitySourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=enabled,
        profile_version=version,
        event_id_property=event_id_property,
        username_property=username_property,
        enabled_event_ids=list(CATALOG if event_ids is None else event_ids),
        validation_status=status,
        validated_profile_version=validated,
        validated_at=NOW,
        validation_report={},
    )
    session.add(row)
    await session.flush()
    return row


async def _mark(session, instance: QRadarInstance, at: datetime) -> CollectionWatermark:
    row = CollectionWatermark(
        instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=at
    )
    session.add(row)
    await session.flush()
    return row


def _collect(session, instance, provider, settings, **over: object):
    values = _settings(**over)
    return IdentityCollector(session, provider, settings=values, clock=lambda: NOW).collect(
        instance
    )


async def _counts(session, source: LogSource) -> dict[int, IdentityEventCount]:
    rows = (
        await session.scalars(
            select(IdentityEventCount).where(IdentityEventCount.log_source_id == source.id)
        )
    ).all()
    return {row.event_id: row for row in rows}


async def _names(session, source: LogSource) -> set[str]:
    rows = (
        await session.scalars(
            select(IdentityTopUsername.username).where(
                IdentityTopUsername.log_source_id == source.id
            )
        )
    ).all()
    return set(rows)


async def _buckets(session) -> list[IdentityBucket]:
    return list((await session.scalars(select(IdentityBucket))).all())


@contextmanager
def _counting(session: object) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _inserts(statements: list[str], table: str) -> int:
    return sum(
        f"INSERT INTO {table}" in sql or f'INSERT INTO "{table}"' in sql for sql in statements
    )


async def test_happy_path_writes_both_sources_from_one_search_each(db_session) -> None:
    instance = await make_instance(db_session)
    dc01 = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    dc02 = await make_log_source(db_session, instance, qradar_id=1004, name="dc02")
    await _profile(db_session, instance, dc01)
    await _profile(db_session, instance, dc02)
    await _mark(db_session, instance, NOW - 2 * WIDTH)
    provider = MockQRadarProvider(seed=1)
    report = await _collect(db_session, instance, provider, _settings())

    assert report.status == "ok"
    assert report.searches_sent == 3
    assert report.splits == 0
    assert report.buckets_written == 2
    kinds = [item.kind for item in provider.identity_searches]
    assert kinds == [
        IdentitySearchKind.COUNTS,
        IdentitySearchKind.RARE_NAMES,
        IdentitySearchKind.VOLUME_NAMES,
    ]
    for item in provider.identity_searches:
        assert "1003" in item.query and "1004" in item.query
        assert "alice" not in item.query and "mallory" not in item.query

    bucket_start = NOW - WIDTH
    buckets = await _buckets(db_session)
    assert {row.bucket_start for row in buckets} == {bucket_start}
    by_source = {row.log_source_id: row for row in buckets}
    assert by_source[dc01.id].population == 15
    assert by_source[dc01.id].measurement == EXACT
    assert by_source[dc02.id].population == 7
    assert "alice" not in str(by_source[dc01.id].query_provenance)
    dc01_counts = await _counts(db_session, dc01)
    assert dc01_counts[4625].event_count == 9
    assert dc01_counts[4740].event_count == 4
    assert dc01_counts[4726].event_count == 0
    assert dc01_counts[4726].measurement == EXACT
    assert await _names(db_session, dc01) == {"alice", "carol", "dave", "eve", "auditor"}
    assert "bob" not in await _names(db_session, dc01)
    assert await _names(db_session, dc02) == {"mallory", "frank"}
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
    )
    assert watermark.watermark_at == bucket_start
    assert watermark.last_error is None


async def test_a_failed_counts_search_is_not_collected_and_is_not_split(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    original = NOW - 2 * WIDTH
    await _mark(db_session, instance, original)
    provider = MockQRadarProvider(seed=1)

    def fault(search):  # type: ignore[no-untyped-def]
        if search.kind is IdentitySearchKind.COUNTS:
            return ProviderError("boom")
        return None

    provider.identity_fault = fault
    report = await _collect(db_session, instance, provider, _settings())
    counts = [item for item in provider.identity_searches if item.kind is IdentitySearchKind.COUNTS]
    assert len(counts) == 1
    assert report.splits == 0
    stored = await _counts(db_session, source)
    assert stored[4625].measurement == MISSING
    assert stored[4625].event_count is None
    assert all(row.event_count is None for row in stored.values())
    assert all(row.measurement == MISSING for row in stored.values())
    buckets = await _buckets(db_session)
    assert buckets[0].population is None
    assert buckets[0].measurement == MISSING
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
    )
    assert watermark.watermark_at == original
    assert watermark.last_error == "ProviderError"
    assert "boom" not in (watermark.last_error or "")


async def test_truncated_counts_split_to_one_bucket_then_stay_truncated(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    await _mark(db_session, instance, NOW - 3 * WIDTH)
    provider = MockQRadarProvider(seed=1)
    provider.identity_force_truncated = lambda search: search.kind is IdentitySearchKind.COUNTS
    report = await _collect(db_session, instance, provider, _settings())
    counts = [item for item in provider.identity_searches if item.kind is IdentitySearchKind.COUNTS]
    assert len(counts) == 3
    assert report.splits == 1
    assert report.searches_sent == 5
    stored = await _counts(db_session, source)
    assert stored[4625].measurement == TRUNCATED
    assert stored[4625].event_count == 9
    assert stored[4726].measurement == TRUNCATED
    assert stored[4726].event_count is None
    assert all(row.measurement == TRUNCATED for row in stored.values())
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
    )
    assert watermark.watermark_at == NOW - WIDTH


async def test_a_watermark_gap_collects_only_the_next_bounded_buckets(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    await _mark(db_session, instance, NOW - timedelta(hours=6))
    provider = MockQRadarProvider(seed=1)
    await _collect(db_session, instance, provider, _settings())
    starts = sorted({row.bucket_start for row in await _buckets(db_session)})
    expected = [NOW - timedelta(hours=6) + WIDTH * step for step in (1, 2, 3)]
    assert starts == expected
    assert NOW - timedelta(hours=6) + 4 * WIDTH not in starts
    stored = await _counts(db_session, source)
    assert stored[4625].measurement == EXACT
    assert stored[4625].event_count == 9
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
    )
    assert watermark.watermark_at == expected[-1]


async def test_ineligible_profiles_are_not_collected(db_session) -> None:
    instance = await make_instance(db_session)
    good = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    disabled = await make_log_source(db_session, instance, qradar_id=1004, name="dc02")
    raw = await make_log_source(db_session, instance, qradar_id=1101, name="unvalidated")
    drifted = await make_log_source(db_session, instance, qradar_id=1102, name="drifted")
    bad_field = await make_log_source(db_session, instance, qradar_id=1103, name="bad-field")
    off_source = await make_log_source(
        db_session, instance, qradar_id=1104, name="off", enabled=False
    )
    empty = await make_log_source(db_session, instance, qradar_id=1105, name="empty")
    await _profile(db_session, instance, good)
    await _profile(db_session, instance, disabled, enabled=False)
    await _profile(
        db_session,
        instance,
        raw,
        status=IdentityProfileValidationStatus.UNVALIDATED.value,
        validated=None,
    )
    await _profile(db_session, instance, drifted, version=2, validated=1)
    await _profile(db_session, instance, bad_field, event_id_property="sourceip")
    await _profile(db_session, instance, off_source)
    await _profile(db_session, instance, empty, event_ids=[])
    await _mark(db_session, instance, NOW - 2 * WIDTH)
    provider = MockQRadarProvider(seed=1)
    report = await _collect(db_session, instance, provider, _settings())

    assert report.status == "ok"
    assert report.buckets_written == 1
    for item in provider.identity_searches:
        assert "1003" in item.query
        assert "1004" not in item.query
        assert "1101" not in item.query
        assert "sourceip" not in item.query
    buckets = await _buckets(db_session)
    assert {row.log_source_id for row in buckets} == {good.id}


async def test_a_held_lock_skips_the_tick(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    await _mark(db_session, instance, NOW - 2 * WIDTH)
    settings = _settings()
    provider = MockQRadarProvider(seed=1)
    key = collector_lock_key(instance.id, COLLECTOR_NAME)
    url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    holder = create_engine(url, future=True)
    conn = holder.connect()
    conn.execute(
        sql_text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
            ns=settings.collection_advisory_lock_namespace, key=key
        )
    )
    try:
        report = await IdentityCollector(
            db_session, provider, settings=settings, clock=lambda: NOW
        ).collect(instance)
        assert report.status == "skipped_locked"
        assert report.skipped_locked is True
        assert provider.identity_searches == []
        assert await _buckets(db_session) == []
    finally:
        conn.execute(
            sql_text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=settings.collection_advisory_lock_namespace, key=key
            )
        )
        conn.close()
        holder.dispose()


async def test_a_spent_budget_writes_no_zero_rows(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    original = NOW - 2 * WIDTH
    await _mark(db_session, instance, original)
    provider = MockQRadarProvider(seed=1)

    def refuse(self: object) -> bool:
        return False

    monkeypatch.setattr("app.identity.search.IdentitySearchBudget.try_take", refuse)
    report = await _collect(db_session, instance, provider, _settings())
    assert report.searches_sent == 0
    assert await _buckets(db_session) == []
    watermark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
    )
    assert watermark.watermark_at == original


async def test_insert_count_stays_one_per_table_as_sources_grow(db_session) -> None:
    instance = await make_instance(db_session)
    await _profile(
        db_session,
        instance,
        await make_log_source(db_session, instance, qradar_id=1003, name="dc01"),
    )
    provider = MockQRadarProvider(seed=1)
    with _counting(db_session) as one_sql:
        await _collect(db_session, instance, provider, _settings())
    await db_session.execute(delete(IdentityTopUsername))
    await db_session.execute(delete(IdentityEventCount))
    await db_session.execute(delete(IdentityBucket))
    await db_session.execute(delete(CollectionWatermark))
    await db_session.flush()
    await _profile(
        db_session,
        instance,
        await make_log_source(db_session, instance, qradar_id=1004, name="dc02"),
    )
    quiet = await make_log_source(db_session, instance, qradar_id=1005, name="quiet")
    await _profile(db_session, instance, quiet)
    await _mark(db_session, instance, NOW - 2 * WIDTH)
    provider = MockQRadarProvider(seed=1)
    with _counting(db_session) as many_sql:
        report = await _collect(db_session, instance, provider, _settings())
    for table in ("identity_bucket", "identity_event_count", "identity_top_username"):
        assert _inserts(one_sql, table) == 1
        assert _inserts(many_sql, table) == 1
    assert report.buckets_written == 3
    quiet_counts = await _counts(db_session, quiet)
    assert quiet_counts[4625].measurement == EXACT
    assert quiet_counts[4625].event_count == 0
    assert len({item.query for item in provider.identity_searches}) == 3
