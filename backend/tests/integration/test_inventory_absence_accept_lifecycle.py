"""D-108/D-109 acceptance lifetime through the collector and operator entries."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.cli import doctor as doctor_cli
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from tests.integration.test_inventory_source_absence import (
    NOW,
    cli,
    mark,
    seed,
    settings,
    spans,
    sync,
)
from tests.integration.test_inventory_source_absence import rig as rig

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def arm(session, instance, *, armed_at=NOW, candidates=()):
    watermark = await mark(session, instance)
    accept = {"armed_at": armed_at.isoformat(), "actor": "operator", "held_count": 21}
    watermark.collection_metadata = {
        **watermark.collection_metadata, "absence_accept": accept,
        "absence_candidates": sorted(map(str, candidates)),
    }
    return watermark, accept


def assert_cleared(report, watermark, reason):
    assert "absence_accept" not in watermark.collection_metadata
    assert report.absence["accept_cleared"] == reason
    assert watermark.collection_metadata["inventory_pass"]["absence"] == report.absence
    assert set(report.absence) == {
        "state", "reason", "candidates", "confirmed", "held_count", "accept_cleared",
    }


@pytest.mark.parametrize("reappeared", [False, True])
async def test_accept_clears_when_nothing_missing(db_session, reappeared):
    instance, provider, sources = await seed(db_session)
    watermark, _ = await arm(db_session, instance,
                             candidates=[sources[3].id] if reappeared else [])
    report = await sync(db_session, instance, provider)
    assert report.absence["state"] == "evaluated"
    assert report.absence["candidates"] == report.absence["confirmed"] == 0
    assert_cleared(report, watermark, "nothing_missing")
    assert await spans(db_session) == []


@pytest.mark.parametrize("offset_us", [-1, 0, 1])
async def test_accept_expiry_boundary_and_breaker_returns(db_session, offset_us):
    instance, provider, _ = await seed(db_session, 23)
    watermark, accept = await arm(db_session, instance, armed_at=NOW - timedelta(hours=24))
    provider.ids = [1, 2]
    now = NOW + timedelta(microseconds=offset_us)
    report = await sync(db_session, instance, provider, now=now)
    assert report.absence["confirmed"] == 0
    assert await spans(db_session) == []
    if offset_us < 0:
        assert report.absence["state"] == "evaluated"
        assert report.absence["candidates"] == 21
        assert watermark.collection_metadata["absence_accept"] == accept
        assert "accept_cleared" not in report.absence
    else:
        assert report.absence["state"] == "held"
        assert report.absence["candidates"] == 0 and report.absence["held_count"] == 21
        assert_cleared(report, watermark, "expired")
        next_pass = await sync(db_session, instance, provider, now=now + timedelta(minutes=5))
        assert next_pass.absence == {
            "state": "held", "reason": "mass_absence_held", "candidates": 0,
            "confirmed": 0, "held_count": 21,
        }
        assert "absence_accept" not in watermark.collection_metadata
        assert await spans(db_session) == []


@pytest.mark.parametrize("first_age,second_age", [
    (timedelta(hours=23, minutes=54), timedelta(hours=24, minutes=6)),
    (timedelta(hours=1), timedelta(hours=49)),
], ids=["crosses-expiry", "collector-gap"])
async def test_stored_candidates_cannot_confirm_under_expired_accept(
    db_session, first_age, second_age,
):
    instance, provider, sources = await seed(db_session, 23)
    armed_at = NOW - first_age
    watermark, accept = await arm(db_session, instance, armed_at=armed_at)
    provider.ids = [1, 2]
    first = await sync(db_session, instance, provider)
    assert first.absence == {
        "state": "evaluated", "reason": "complete", "candidates": 21,
        "confirmed": 0, "held_count": 0,
    }
    assert watermark.collection_metadata["absence_accept"] == accept
    assert watermark.collection_metadata["absence_candidates"] == sorted(
        str(source.id) for q, source in sources.items() if q > 2)
    assert await spans(db_session) == []
    second_time = armed_at + second_age
    second = await sync(db_session, instance, provider, now=second_time)
    assert second.absence["state"] == "held"
    assert second.absence["confirmed"] == 0 and second.absence["held_count"] == 21
    assert_cleared(second, watermark, "expired")
    assert await spans(db_session) == []
    third = await sync(db_session, instance, provider, now=second_time + timedelta(minutes=5))
    assert third.absence == {
        "state": "held", "reason": "mass_absence_held", "candidates": 21,
        "confirmed": 0, "held_count": 21,
    }
    assert "absence_accept" not in watermark.collection_metadata
    assert await spans(db_session) == []


@pytest.mark.parametrize("bad", [
    "partial", "unvalidated", "zero", "failed", "types", "dropped", "duplicate",
])
@pytest.mark.parametrize("arm_time", ["expired", "missing", "malformed", "naive"])
async def test_invalid_pass_preserves_stale_accept(db_session, bad, arm_time):
    instance, provider, _ = await seed(db_session)
    watermark, accept = await arm(db_session, instance, armed_at=NOW - timedelta(days=2))
    if arm_time != "expired":
        accept = dict(accept)
        if arm_time == "missing":
            accept.pop("armed_at")
        else:
            accept["armed_at"] = "bad" if arm_time == "malformed" else NOW.replace(
                tzinfo=None).isoformat()
        watermark.collection_metadata = {**watermark.collection_metadata, "absence_accept": accept}
    if bad == "partial":
        provider.complete = False
    elif bad == "unvalidated":
        provider.validated = False
    elif bad == "zero":
        provider.ids = []
    elif bad == "failed":
        provider.failed = True
    elif bad == "types":
        provider.types_failed = True
    elif bad == "dropped":
        provider.rows_dropped = 1
    else:
        provider.duplicate_ids = 1
    report = await sync(db_session, instance, provider)
    assert report.absence["state"] == "skipped"
    assert watermark.collection_metadata["absence_accept"] == accept
    assert "accept_cleared" not in report.absence
    assert await spans(db_session) == []


@pytest.mark.parametrize("missing", [False, True])
async def test_switch_off_preserves_expired_accept(db_session, missing):
    instance, provider, sources = await seed(db_session)
    watermark, accept = await arm(db_session, instance, armed_at=NOW - timedelta(days=2),
                                 candidates=[sources[3].id])
    before = dict(watermark.collection_metadata)
    if missing:
        provider.ids = [1, 2]
    report = await sync(db_session, instance, provider, on=False)
    assert report.absence is None
    assert watermark.collection_metadata["absence_accept"] == accept
    assert {k: v for k, v in watermark.collection_metadata.items() if k != "inventory_pass"} == {
        k: v for k, v in before.items() if k != "inventory_pass"
    }
    assert "absence" not in watermark.collection_metadata["inventory_pass"]
    assert await spans(db_session) == []


@pytest.mark.parametrize("age_hours", [0, 24])
async def test_confirmation_clears_accept_with_reason(db_session, age_hours):
    instance, provider, sources = await seed(db_session)
    watermark, _ = await arm(db_session, instance,
                             armed_at=NOW - timedelta(hours=age_hours), candidates=[sources[3].id])
    provider.ids = [1, 2]
    report = await sync(db_session, instance, provider)
    assert report.absence["confirmed"] == 1
    assert_cleared(report, watermark, "confirmed" if age_hours < 24 else "expired")
    assert len(await spans(db_session)) == 1


@pytest.mark.parametrize("accept", [None, "invalid"])
async def test_no_armed_accept_has_no_clear_reason(db_session, accept):
    instance, provider, _ = await seed(db_session)
    watermark = await mark(db_session, instance)
    if accept is not None:
        watermark.collection_metadata = {**watermark.collection_metadata, "absence_accept": accept}
    before = dict(watermark.collection_metadata)
    report = await sync(db_session, instance, provider)
    assert "accept_cleared" not in report.absence
    assert watermark.collection_metadata.get("absence_accept") == before.get("absence_accept")


@pytest.mark.parametrize("armed_at", ["missing", None, 123, "invalid", "2026-11-20T12:01:00"])
@pytest.mark.parametrize("missing", [False, True])
async def test_unreadable_arm_time_is_not_honoured_and_clears(db_session, armed_at, missing):
    instance, provider, _ = await seed(db_session, 23)
    watermark, accept = await arm(db_session, instance)
    accept = dict(accept)
    if armed_at == "missing":
        accept.pop("armed_at")
    else:
        accept["armed_at"] = armed_at
    watermark.collection_metadata = {**watermark.collection_metadata, "absence_accept": accept}
    if missing:
        provider.ids = [1, 2]
    report = await sync(db_session, instance, provider)
    assert report.absence["state"] == ("held" if missing else "evaluated")
    assert report.absence["confirmed"] == report.absence["candidates"] == 0
    assert report.absence["held_count"] == (21 if missing else 0)
    assert_cleared(report, watermark, "unreadable_arm_time")
    assert await spans(db_session) == []


@pytest.mark.parametrize("valid_time", [False, True])
async def test_show_entry_reports_age_expiry_read_only(rig, db_schema, valid_time):
    _, maker = rig
    async with maker() as session:
        instance, _, _ = await seed(session)
        watermark, accept = await arm(session, instance)
        if not valid_time:
            watermark.collection_metadata = {**watermark.collection_metadata,
                                             "absence_accept": {**accept, "armed_at": "bad"}}
        before = dict(watermark.collection_metadata)
        name = instance.name
        await session.commit()
    before_show = datetime.now(UTC)
    result = cli(db_schema, "show", "--instance", name)
    after_show = datetime.now(UTC)
    assert result.returncode == 0 and "accept armed: yes" in result.stdout
    if valid_time:
        age = result.stdout.split("Accept age: ")[1].split(" seconds;")[0]
        assert int((before_show - NOW).total_seconds()) <= int(age) <= int(
            (after_show - NOW).total_seconds())
        assert f"expires at: {(NOW + timedelta(hours=24)).isoformat()}" in result.stdout
        assert "cleared on the next complete validated nonempty pass" in result.stdout
    else:
        assert ("Accept age: unavailable; expires at: unavailable (arm time invalid)."
                in result.stdout)
    assert "source-" not in result.stdout and "operator" not in result.stdout
    async with maker() as session:
        assert (await session.scalar(select(CollectionWatermark))).collection_metadata == before


async def test_doctor_entry_explains_expiry_then_stops_reporting_armed(rig, monkeypatch, capsys):
    _, maker = rig
    async with maker() as session:
        instance, provider, _ = await seed(session)
        instance_id = instance.id
        await arm(session, instance, armed_at=NOW - timedelta(days=2))
        provider.validated = False
        await sync(session, instance, provider)
        await session.commit()
    doctor = doctor_cli.Doctor(settings=settings(), sessionmaker=maker, now=lambda: NOW)
    for name, _ in doctor_cli._CHECK_RUNBOOKS:
        if name != "watermarks":
            monkeypatch.setattr(doctor, f"check_{name}", AsyncMock(return_value=
                doctor_cli.CheckResult(name, doctor_cli.Status.PASS, "fixture")))
    monkeypatch.setattr(doctor_cli, "Doctor", lambda: doctor)
    monkeypatch.setattr(doctor_cli, "dispose_engine", AsyncMock())
    assert await doctor_cli._async_main(False) == 1
    output = capsys.readouterr().out
    assert "absence accept armed for 1 instance(s), 21 held sources" in output
    assert ("Acceptance expires 24 hours after arming; the next complete validated nonempty "
            "inventory pass after that clears it.") in output
    assert "source-" not in output and "operator" not in output
    async with maker() as session:
        instance = await session.get(QRadarInstance, instance_id)
        provider.validated = True
        await sync(session, instance, provider)
        await session.commit()
    assert await doctor_cli._async_main(False) == 0
    assert "absence accept armed" not in capsys.readouterr().out


async def test_show_entry_clamps_future_arm_age(rig, db_schema):
    _, maker = rig
    future = datetime.now(UTC) + timedelta(hours=1)
    async with maker() as session:
        instance, _, _ = await seed(session)
        watermark, _ = await arm(session, instance, armed_at=future)
        before = dict(watermark.collection_metadata)
        name = instance.name
        await session.commit()
    result = cli(db_schema, "show", "--instance", name)
    assert result.returncode == 0
    assert "Accept age: 0 seconds;" in result.stdout
    assert f"expires at: {(future + timedelta(hours=24)).isoformat()}" in result.stdout
    async with maker() as session:
        assert (await session.scalar(select(CollectionWatermark))).collection_metadata == before


async def test_accept_entry_explains_expiry(rig, db_schema):
    _, maker = rig
    async with maker() as session:
        instance, provider, _ = await seed(session, 23)
        name = instance.name
        provider.ids = [1, 2]
        assert (await sync(session, instance, provider)).absence["state"] == "held"
        await session.commit()
    result = cli(db_schema, "accept", "--instance", name)
    assert result.returncode == 0
    assert result.stdout == (
        "Absence accept armed for 21 held sources. The breaker is lifted until "
        "a complete validated pass confirms absence or finds nothing missing, or until the "
        "accept expires 24 hours after arming; two-pass evidence is required.\n"
    )
    async with maker() as session:
        assert "absence_accept" in (await session.scalar(select(CollectionWatermark))
                                   ).collection_metadata
