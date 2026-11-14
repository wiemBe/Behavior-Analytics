"""Daily identity revalidation: status, skip, resume, and a bounded probe."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.identity.collector import COLLECTOR_NAME, IdentityCollector
from app.identity.dto import IdentityFieldProbe, IdentityProbeResult
from app.identity.revalidation import LOCK_NAME, IdentityRevalidator
from app.models.enums import (
    IDENTITY_EVENT_CATALOG,
    IdentityMeasurement,
    IdentityProfileValidationStatus,
)
from app.models.identity import AuditLog
from app.models.identity_detection import IdentityBucket, IdentityEventCount, IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.security.rbac import Principal
from app.services.ariel_permit import ArielPermitUnavailable
from app.services.locks import collector_lock_key
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client, run

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
WIDTH = timedelta(seconds=300)
CATALOG = [event_id for event_id, _kind in IDENTITY_EVENT_CATALOG]
EXACT = IdentityMeasurement.EXACT.value
VALID = IdentityProfileValidationStatus.VALID.value
INVALID = IdentityProfileValidationStatus.INVALID.value
INCONCLUSIVE = IdentityProfileValidationStatus.INCONCLUSIVE.value
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))


def _settings(**over: object) -> Settings:
    values: dict[str, object] = {
        "search_cadence_settle_seconds": 0,
        "collection_interval_seconds": 300,
        "collection_max_backfill_intervals": 0,
        "identity_max_sources_per_search": 50,
        "ariel_multi_bucket_max_rows": 5000,
        "search_cadence_max_searches_per_tick": 16,
        "identity_collection_enabled": False,
        "identity_revalidation_interval_seconds": 86400,
    }
    values.update(over)
    return Settings(**values)  # type: ignore[arg-type]


def _probe(**over: object) -> IdentityProbeResult:
    values: dict[str, object] = {
        "total_events": 100,
        "event_id": IdentityFieldProbe(95, False, None),
        "username": IdentityFieldProbe(92, False, None),
        "catalog_event_ids": (4625, 4740, 4720, 1102),
        "truncated": False,
        "error": None,
        "searches": 4,
    }
    values.update(over)
    return IdentityProbeResult(**values)  # type: ignore[arg-type]


def _script(provider: MockQRadarProvider, calls: list[int], answers: dict[int, object]) -> None:
    async def run(**kwargs: object) -> IdentityProbeResult:
        source_id = int(kwargs["qradar_log_source_id"])  # type: ignore[arg-type]
        calls.append(source_id)
        answer = answers[source_id]
        if isinstance(answer, Exception):
            raise answer
        assert isinstance(answer, IdentityProbeResult)
        return answer

    provider.probe_identity_fields = run  # type: ignore[method-assign]


class _Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: int) -> None:
        self.moment += timedelta(seconds=seconds)


async def _profile(
    session,
    instance: QRadarInstance,
    source: LogSource,
    *,
    enabled: bool = True,
    status: str = VALID,
    validated_at: datetime = NOW,
) -> IdentitySourceProfile:
    row = IdentitySourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=enabled,
        profile_version=1,
        event_id_property="EventID",
        username_property="username",
        enabled_event_ids=list(CATALOG),
        validation_status=status,
        validated_profile_version=1,
        validated_at=validated_at,
        validation_report={},
    )
    session.add(row)
    await session.flush()
    return row


async def _mark(session, instance: QRadarInstance, at: datetime) -> None:
    session.add(
        CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=at)
    )
    await session.flush()


async def _collect(session, instance, provider, moment: datetime):
    return await IdentityCollector(
        session, provider, settings=_settings(), clock=lambda: moment
    ).collect(instance)


async def _buckets(session) -> list[IdentityBucket]:
    return list((await session.scalars(select(IdentityBucket))).all())


async def test_a_valid_profile_stays_valid_and_the_probe_is_logged(
    db_session, caplog: pytest.LogCaptureFixture
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    profile = await _profile(db_session, instance, source, validated_at=NOW - timedelta(days=1))
    provider = MockQRadarProvider(seed=1)
    with caplog.at_level(logging.INFO, logger="app.identity.revalidation"):
        report = await IdentityRevalidator(
            db_session, settings=_settings(), clock=lambda: NOW
        ).run(instance, provider)

    assert report.status == "ok"
    assert report.probed == 1
    assert report.searches == 4
    assert report.changed == 0
    assert report.failed == 0
    assert report.sources == [
        {"qradar_log_source_id": 1003, "old_status": VALID, "new_status": VALID, "searches": 4}
    ]
    assert profile.validation_status == VALID
    assert profile.enabled is True
    assert profile.validated_at == NOW
    assert profile.validated_profile_version == profile.profile_version
    assert profile.validation_report["revalidation_slot"] == int(NOW.timestamp()) // 86400
    assert "revalidation_error" not in profile.validation_report
    assert "searches 4" in caplog.text
    assert "1003" in caplog.text
    assert "alice" not in caplog.text
    audits = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.action == "identity.profile.revalidated")
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].object_id == str(profile.id)
    assert audits[0].detail["qradar_log_source_id"] == 1003
    assert audits[0].detail["old_status"] == VALID
    assert audits[0].detail["new_status"] == VALID
    assert "alice" not in str(audits[0].detail)
    validated = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "identity.profile.validated")
    )
    assert validated is None


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (
            _probe(
                total_events=1220,
                event_id=IdentityFieldProbe(1220, False, None),
                username=IdentityFieldProbe(1200, False, None),
                catalog_event_ids=(),
            ),
            INVALID,
        ),
        (
            _probe(
                total_events=0,
                event_id=IdentityFieldProbe(0, False, None),
                username=IdentityFieldProbe(0, False, None),
                catalog_event_ids=(),
            ),
            INCONCLUSIVE,
        ),
    ],
)
async def test_a_failed_revalidation_is_not_collected_as_zero(db_session, answer, status) -> None:
    """An ineligible source is omitted. The next tick writes no row and no exact zero."""
    instance = await make_instance(db_session)
    drifted = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    sibling = await make_log_source(db_session, instance, qradar_id=1004, name="dc02")
    bad = await _profile(db_session, instance, drifted)
    await _profile(db_session, instance, sibling)
    await _mark(db_session, instance, NOW - 2 * WIDTH)
    collector = MockQRadarProvider(seed=1)
    await _collect(db_session, instance, collector, NOW)
    checker = MockQRadarProvider(seed=1)
    _script(checker, [], {1003: answer, 1004: _probe()})
    report = await IdentityRevalidator(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run(instance, checker)

    assert report.changed == 1
    assert bad.validation_status == status
    assert bad.enabled is True
    kept = await db_session.scalar(
        select(IdentitySourceProfile).where(IdentitySourceProfile.log_source_id == sibling.id)
    )
    assert kept is not None and kept.validation_status == VALID and kept.enabled is True
    before = len(collector.identity_searches)
    await _collect(db_session, instance, collector, NOW + WIDTH)
    fresh = collector.identity_searches[before:]
    assert fresh
    for item in fresh:
        assert "1003" not in item.query
        assert "1004" in item.query
    rows = await _buckets(db_session)
    assert {row.bucket_start for row in rows if row.log_source_id == drifted.id} == {NOW - WIDTH}
    assert {row.bucket_start for row in rows if row.log_source_id == sibling.id} == {
        NOW - WIDTH,
        NOW,
    }
    counts = (
        await db_session.scalars(
            select(IdentityEventCount).where(IdentityEventCount.log_source_id == drifted.id)
        )
    ).all()
    assert counts
    assert all(row.bucket_start != NOW for row in counts)


async def test_an_invalid_profile_resumes_when_revalidation_is_valid(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    profile = await _profile(db_session, instance, source, status=INVALID)
    await _mark(db_session, instance, NOW - 2 * WIDTH)
    provider = MockQRadarProvider(seed=1)
    report = await IdentityRevalidator(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run(instance, provider)

    assert report.changed == 1
    assert report.sources[0]["old_status"] == INVALID
    assert report.sources[0]["new_status"] == VALID
    assert profile.validation_status == VALID
    assert profile.enabled is True
    assert profile.profile_version == 1
    assert profile.validated_profile_version == 1
    collected = await _collect(db_session, instance, provider, NOW)
    assert collected.buckets_written == 1
    stored = (
        await db_session.scalars(
            select(IdentityEventCount).where(
                IdentityEventCount.log_source_id == source.id,
                IdentityEventCount.event_id == 4625,
            )
        )
    ).one()
    assert stored.event_count == 9
    assert stored.measurement == EXACT


async def test_a_transport_failure_keeps_the_status_and_records_a_short_reason(
    db_session, caplog: pytest.LogCaptureFixture
) -> None:
    instance = await make_instance(db_session)
    field = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    raised = await make_log_source(db_session, instance, qradar_id=1004, name="dc02")
    permit = await make_log_source(db_session, instance, qradar_id=1101, name="permit")
    first = await _profile(db_session, instance, field, validated_at=NOW - timedelta(days=1))
    second = await _profile(db_session, instance, raised, validated_at=NOW - timedelta(days=1))
    third = await _profile(db_session, instance, permit, validated_at=NOW - timedelta(days=1))
    provider = MockQRadarProvider(seed=1)
    calls: list[int] = []
    refused = ArielPermitUnavailable(1.0, 1, 1)
    _script(
        provider,
        calls,
        {
            1003: _probe(
                username=IdentityFieldProbe(None, False, "ProviderUnavailableError"), searches=3
            ),
            1004: ProviderError("alice searched"),
            1101: refused,
        },
    )
    with caplog.at_level(logging.INFO, logger="app.identity.revalidation"):
        report = await IdentityRevalidator(
            db_session, settings=_settings(), clock=lambda: NOW
        ).run(instance, provider)

    assert sorted(calls) == [1003, 1004, 1101]
    assert report.failed == 3
    assert report.changed == 0
    for profile in (first, second, third):
        assert profile.validation_status == VALID
        assert profile.enabled is True
        assert profile.validated_at == NOW - timedelta(days=1)
        assert profile.validated_profile_version == 1
    assert first.validation_report["revalidation_error"] == "provider_unavailable"
    assert second.validation_report["revalidation_error"] == "provider_error"
    assert third.validation_report["revalidation_error"] == "permit_refused"
    assert "alice" not in str(first.validation_report)
    assert "alice" not in str(second.validation_report)
    assert "alice" not in str(third.validation_report)
    assert str(refused) not in str(third.validation_report)
    assert "alice" not in caplog.text
    audits = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.action == "identity.profile.revalidation_failed")
        )
    ).all()
    assert {row.detail["reason"] for row in audits} == {
        "provider_unavailable",
        "provider_error",
        "permit_refused",
    }
    assert all(row.outcome == "FAILURE" for row in audits)
    assert all(row.detail["status"] == VALID for row in audits)
    assert "alice" not in str([row.detail for row in audits])
    seen = list(calls)
    again = await IdentityRevalidator(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run(instance, provider)
    assert again.probed == 0
    assert again.searches == 0
    assert calls == seen


async def test_a_rejected_field_is_invalid_even_when_the_probe_has_an_error(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    profile = await _profile(db_session, instance, source)
    provider = MockQRadarProvider(seed=1)
    _script(
        provider,
        [],
        {
            1003: _probe(
                event_id=IdentityFieldProbe(None, True, "ProviderQueryRejectedError"),
                error="ProviderUnavailableError",
                searches=2,
            )
        },
    )
    report = await IdentityRevalidator(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run(instance, provider)
    assert report.failed == 0
    assert report.changed == 1
    assert profile.validation_status == INVALID
    assert profile.enabled is True
    assert profile.validation_report.get("revalidation_error") is None


async def test_disabled_profiles_are_not_probed(db_session) -> None:
    instance = await make_instance(db_session)
    on = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    off = await make_log_source(db_session, instance, qradar_id=1004, name="dc02")
    await _profile(db_session, instance, on)
    disabled = await _profile(db_session, instance, off, enabled=False, status=VALID)
    provider = MockQRadarProvider(seed=1)
    calls: list[int] = []
    _script(provider, calls, {1003: _probe(), 1004: _probe()})
    report = await IdentityRevalidator(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run(instance, provider)
    assert calls == [1003]
    assert report.probed == 1
    assert report.searches == 4
    assert disabled.validation_status == VALID
    assert disabled.validation_report == {}


async def test_a_provider_without_ariel_does_not_search(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    profile = await _profile(db_session, instance, source)
    calls = {"n": 0}

    async def boom(self, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise AssertionError("mcp search")

    monkeypatch.setattr(QRadarMCPProvider, "probe_identity_fields", boom)
    provider = QRadarMCPProvider(base_url="http://127.0.0.1:9", sec_token="not-a-real-token")
    try:
        report = await IdentityRevalidator(
            db_session, settings=_settings(), clock=lambda: NOW
        ).run(instance, provider)
    finally:
        await provider.aclose()
    assert calls["n"] == 0
    assert report.status == "unsupported"
    assert report.searches == 0
    assert report.probed == 0
    assert profile.validation_status == VALID
    assert profile.validation_report == {}


async def test_a_second_run_in_the_same_slot_does_not_probe(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    provider = MockQRadarProvider(seed=1)
    calls: list[int] = []
    _script(provider, calls, {1003: _probe()})
    clock = _Clock(NOW)
    settings = _settings()
    first = await IdentityRevalidator(db_session, settings=settings, clock=clock).run(
        instance, provider
    )
    second = await IdentityRevalidator(db_session, settings=settings, clock=clock).run(
        instance, provider
    )
    assert first.probed == 1 and first.searches == 4
    assert second.probed == 0 and second.searches == 0 and second.skipped == 1
    assert calls == [1003]
    clock.advance(settings.identity_revalidation_interval_seconds)
    third = await IdentityRevalidator(db_session, settings=settings, clock=clock).run(
        instance, provider
    )
    assert third.probed == 1 and third.searches == 4
    assert calls == [1003, 1003]


async def test_a_held_lock_does_not_probe(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    await _profile(db_session, instance, source)
    provider = MockQRadarProvider(seed=1)
    calls: list[int] = []
    _script(provider, calls, {1003: _probe()})
    settings = _settings()
    key = collector_lock_key(instance.id, LOCK_NAME)
    url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    holder = create_engine(url, future=True)
    conn = holder.connect()
    conn.execute(
        sql_text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
            ns=settings.collection_advisory_lock_namespace, key=key
        )
    )
    try:
        report = await IdentityRevalidator(
            db_session, settings=settings, clock=lambda: NOW
        ).run(instance, provider)
    finally:
        conn.execute(
            sql_text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=settings.collection_advisory_lock_namespace, key=key
            )
        )
        conn.close()
        holder.dispose()
    assert report.status == "skipped_locked"
    assert report.searches == 0
    assert calls == []


def test_source_detail_shows_the_revalidated_status(db_schema) -> None:  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=1003, name="dc01")
        await _profile(session, instance, source, validated_at=NOW - timedelta(days=1))
        provider = MockQRadarProvider(seed=1)
        _script(
            provider,
            [],
            {
                1003: _probe(
                    total_events=1220,
                    event_id=IdentityFieldProbe(1220, False, None),
                    username=IdentityFieldProbe(1200, False, None),
                    catalog_event_ids=(),
                )
            },
        )
        await IdentityRevalidator(session, settings=_settings(), clock=lambda: NOW).run(
            instance, provider
        )
        return source.id

    source_id = run(work)
    client = Client(READER)
    response = client.get(f"/api/v1/identity/sources/{source_id}")
    assert response.status_code == 200, response.text
    body = response.json()["profile"]
    assert body["validation_status"] == INVALID
    assert body["enabled"] is True
    assert body["validated_at"].startswith("2026-11-13T12:00:00")
    assert "alice" not in response.text


async def test_the_task_probes_enabled_profiles_and_skips_mcp(
    db_session, db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = await make_instance(db_session)
    live = await make_log_source(db_session, mock, qradar_id=1003, name="dc01")
    quiet = await make_log_source(db_session, mock, qradar_id=1004, name="dc02")
    live_profile = await _profile(
        db_session, mock, live, validated_at=datetime(2026, 11, 1, tzinfo=UTC)
    )
    quiet_profile = await _profile(db_session, mock, quiet, enabled=False)
    mcp = QRadarInstance(
        name=f"mcp-{mock.name}",
        console_host="mcp.invalid",
        provider_kind="mcp",
        sec_token="not-a-real-token",
        mcp_base_url="http://127.0.0.1:9",
        enabled=True,
    )
    db_session.add(mcp)
    await db_session.flush()
    mcp_source = await make_log_source(db_session, mcp, qradar_id=1003, name="mcp-dc")
    mcp_profile = await _profile(db_session, mcp, mcp_source)
    await db_session.commit()
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    calls = {"n": 0}

    async def boom(self, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise AssertionError("mcp search")

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: _settings())
    monkeypatch.setattr(QRadarMCPProvider, "probe_identity_fields", boom)
    try:
        result = await tasks.revalidate_identity_profiles()
    finally:
        await engine.dispose()

    by_name = {item["instance"]: item for item in result["results"]}
    assert result["instances"] == 2
    assert by_name[mock.name]["status"] == "ok"
    assert by_name[mock.name]["probed"] == 1
    assert by_name[mock.name]["searches"] == 4
    assert by_name[mcp.name]["status"] == "unsupported"
    assert by_name[mcp.name]["searches"] == 0
    assert by_name[mcp.name]["probed"] == 0
    assert calls["n"] == 0
    db_session.expire_all()
    await db_session.refresh(live_profile)
    await db_session.refresh(quiet_profile)
    await db_session.refresh(mcp_profile)
    assert live_profile.validation_status == VALID
    assert live_profile.validated_at is not None
    assert live_profile.validated_at > datetime(2026, 11, 1, tzinfo=UTC)
    assert quiet_profile.validation_report == {}
    assert mcp_profile.validation_status == VALID
    assert mcp_profile.validation_report == {}
