"""SCALE-2c: exact name checks and fail-closed snapshot state mapping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.property_catalog import stored_property_names

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
NAMES = ["Metric ID", "Value", "Element", "Source Geographic Location"]


def name_session(*, missing=(), status="COMPLETE", as_of=NOW, complete=True, failure=None):
    session = MagicMock()
    state = SimpleNamespace(status=status, as_of=as_of, applicability_complete=complete,
                            failure=failure)
    rows = [(state, name) for name in NAMES if name not in missing] or [(state, None)]
    session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    return session


@pytest.mark.parametrize(("status", "stamp", "complete", "failure", "expected"), [
    ("COMPLETE", NOW, True, None, "COMPLETE"),
    ("COMPLETE", NOW - timedelta(days=30), True, None, "STALE"),
    ("COMPLETE", NOW - timedelta(seconds=599), True, None, "COMPLETE"),
    ("COMPLETE", NOW - timedelta(seconds=600), True, None, "STALE"),
    ("REFRESHING", NOW, True, None, "REFRESHING"),
    ("NEVER", None, False, None, "NEVER"),
    ("FAILED", NOW, True, {"kind": "timeout"}, "FAILED"),
    ("FAILED", NOW, True, None, "FAILED"),
    ("PARTIAL", NOW, False, {"kind": "ceiling"}, "PARTIAL"),
    ("PARTIAL", NOW, True, None, "PARTIAL"),
    ("COMPLETE", None, True, None, "NEVER"),
    ("COMPLETE", NOW, False, None, "PARTIAL"),
    ("REFRESHING", None, False, None, "NEVER"),
    ("REFRESHING", NOW, True, {"kind": "timeout"}, "PARTIAL"),
    ("UNKNOWN", NOW, True, None, "UNKNOWN"),
])
async def test_snapshot_states_are_explicit_and_unknown_names_are_never_missing(
    status, stamp, complete, failure, expected,
):
    session = name_session(status=status, as_of=stamp, complete=complete, failure=failure)
    result = await stored_property_names(session, instance_id=uuid.uuid4(),
                                         names=["Value", "absent"], now=NOW, refresh_seconds=600)
    assert (result.status, result.as_of) == (expected, stamp)
    readable = expected in {"COMPLETE", "STALE", "REFRESHING"}
    assert result.readable is readable
    assert result.present == ({"Value": True, "absent": False} if readable else
                              {"Value": None, "absent": None})
    session.execute.assert_awaited_once()


async def test_never_built_snapshot_without_a_state_row():
    session = name_session()
    session.execute.return_value.all = lambda: []
    result = await stored_property_names(session, instance_id=uuid.uuid4(), names=NAMES,
                                         now=NOW, refresh_seconds=600)
    assert result.status == "NEVER" and result.as_of is None and not result.readable
    assert result.present == dict.fromkeys(NAMES)


@pytest.mark.parametrize(("status", "stamp", "missing", "expected"), [
    ("COMPLETE", NOW, (), "present"),
    ("COMPLETE", NOW - timedelta(days=30), (), "present"),
    ("COMPLETE", NOW, ("Value", "Source Geographic Location"), "missing"),
    ("NEVER", None, (), "unreadable"),
    ("FAILED", NOW, (), "unreadable"),
    ("PARTIAL", NOW, (), "unreadable"),
    ("REFRESHING", None, (), "unreadable"),
])
async def test_each_caller_maps_snapshot_guards_without_property_requests(
    monkeypatch, status, stamp, missing, expected,
):
    from app.cli.doctor import Doctor, Status
    from app.collectors.geo_collector import GeoCollector
    from app.collectors.qradar_health_collector import QRadarHealthCollector, QRadarHealthReport
    from app.core.config import get_settings
    from app.geo.fold import GeoDirection
    from app.providers.mock import MockQRadarProvider

    instance_id = uuid.uuid4()
    settings = get_settings().model_copy(update={"geo_country_source": "property",
                                                 "geo_directions": ["SOURCE"]})
    session = name_session(status=status, as_of=stamp, missing=missing)
    provider = MockQRadarProvider(seed=1, now=NOW)
    provider.list_custom_properties = AsyncMock(side_effect=AssertionError("property walk"))
    provider.read_property_catalog = AsyncMock(side_effect=AssertionError("expression walk"))
    health = QRadarHealthCollector(session, provider, settings=settings, clock=lambda: NOW)
    result, folded = await health._collect_metrics(NOW - timedelta(minutes=15), NOW,
                                                  QRadarHealthReport(instance_id=instance_id))
    if expected == "present":
        assert result.state == "OK" and folded is not None
    else:
        assert result.state == "NOT_COLLECTED" and folded is None
        assert result.reason.code == "qradar_health_detail.reason.property_" + expected
        assert provider.health_searches == []
    geo = GeoCollector(session, provider, settings=settings, clock=lambda: NOW)
    found, failure = await geo._country_catalog(instance_id, [GeoDirection.SOURCE], True)
    if expected == "unreadable":
        assert found is None and failure.code == "geo.property.unreadable"
    else:
        assert failure is None
        assert ("Source Geographic Location" in found) is (expected == "present")
    session.__aenter__.return_value = session
    factory = MagicMock(side_effect=AssertionError("provider built"))
    doctor = Doctor(settings=settings, sessionmaker=lambda: session,
                    provider_factory=factory, now=lambda: NOW)
    monkeypatch.setattr(doctor, "_enabled_instances", AsyncMock(return_value=[
        SimpleNamespace(id=instance_id, name="lab"),
    ]))
    line = await doctor.check_geo_country_source()
    assert line.status == {"present": Status.PASS, "missing": Status.WARN,
                           "unreadable": Status.FAIL}[expected]
    if expected == "unreadable":
        assert "property_unreadable" in line.message
    for method in (provider.list_custom_properties, provider.read_property_catalog):
        method.assert_not_called()
    factory.assert_not_called()
