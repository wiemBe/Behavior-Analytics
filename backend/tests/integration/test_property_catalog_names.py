"""SCALE-2c: database-backed name guards through the collectors and doctor."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli.doctor import Doctor, Status, render_text
from app.collectors.geo_collector import GeoCollector
from app.collectors.qradar_health_collector import QRadarHealthCollector
from app.core.config import get_settings
from app.geo.fold import GeoDirection
from app.models.geo import GeoCountryBucket
from app.models.property_catalog import PropertyCatalogEntry, PropertyCatalogState
from app.models.qradar_health import QRadarHealthMetricBucket, QRadarHealthRun
from app.providers.base import ProviderCapability
from app.providers.mock import MockQRadarProvider
from app.services.property_catalog import stored_property_names
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 20, 12, 17, tzinfo=UTC)
NAMES = ["Metric ID", "Value", "Element", "Source Geographic Location", "Unused Property"]


async def catalog_instance(session, *, now=NOW, **kwargs):
    """Seed stored metadata independently of the provider under test."""
    instance = await make_instance(session)
    for key, value in kwargs.items():
        setattr(instance, key, value)
    session.add(PropertyCatalogState(instance_id=instance.id, status="COMPLETE", as_of=now,
                                     applicability_complete=True))
    session.add_all([PropertyCatalogEntry(
        instance_id=instance.id, qradar_id=i, identifier=f"id{i}", name=name,
        property_type="string", name_allowed=True, type_ids=[],
        use_for_rule_engine=False, auto_discovered=False,
    ) for i, name in enumerate(NAMES)])
    await session.flush()
    return instance


class NoPropertyRequests(MockQRadarProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.property_calls = []

    async def list_custom_properties(self, *, log_source_type_id=None):
        self.property_calls.append("list")
        raise AssertionError("name checks must not walk properties or expressions")

    async def read_property_catalog(self):
        self.property_calls.append("refresh")
        raise AssertionError("name checks must not refresh the catalog")


@pytest.mark.parametrize(("state", "stamp", "missing", "complete"), [
    ("COMPLETE", NOW, False, True),
    ("COMPLETE", NOW - timedelta(days=30), False, True),
    ("COMPLETE", NOW, True, True),
    ("NEVER", None, False, False),
    ("FAILED", NOW, False, True),
    ("PARTIAL", NOW, False, True),
    ("PARTIAL", NOW, False, False),
    ("REFRESHING", NOW, False, True),
    ("REFRESHING", None, False, False),
    (None, None, False, False),
])
async def test_collectors_and_doctor_use_only_stored_names(
    db_session, state, stamp, missing, complete,
):
    instance = await catalog_instance(db_session)
    if state is None:
        await db_session.execute(delete(PropertyCatalogState))
    else:
        await db_session.execute(update(PropertyCatalogState).values(
            status=state, as_of=stamp, applicability_complete=complete,
            failure={"kind": "ceiling"} if state == "PARTIAL" else None,
        ))
    if missing:
        await db_session.execute(delete(PropertyCatalogEntry).where(
            PropertyCatalogEntry.name.in_(["Value", "Source Geographic Location"])
        ))
    await db_session.commit()
    settings = get_settings().model_copy(update={"geo_country_source": "property",
                                                 "geo_directions": ["SOURCE"]})
    provider = NoPropertyRequests(seed=1, now=NOW)
    provider.capabilities = provider.capabilities - {
        # Stored names do not need the provider's catalog capability.
        ProviderCapability.CUSTOM_PROPERTY_CATALOG,
    }
    health = QRadarHealthCollector(db_session, provider, settings=settings, clock=lambda: NOW,
                                   health_provider_factory=lambda _: NoPropertyRequests())
    geo = GeoCollector(db_session, provider, settings=settings, clock=lambda: NOW)
    h_report = await health.collect(instance)
    g_report = await geo.collect(instance)
    readable = state in {"COMPLETE", "REFRESHING"} and stamp is not None
    run = await db_session.scalar(select(QRadarHealthRun))
    if not readable or missing:
        code = "property_missing" if readable else "property_unreadable"
        assert run.metrics_state == "NOT_COLLECTED"
        assert run.metrics_reason_code == "qradar_health_detail.reason." + code
        assert h_report.searches_sent == 1  # Notifications remain independent.
        assert provider.geo_searches == [] and g_report.searches_sent == 0
        assert await db_session.scalar(select(
            func.count()).select_from(QRadarHealthMetricBucket)) == 0
        assert await db_session.scalar(select(
            func.count()).select_from(GeoCountryBucket)) == 0
        wm = (await geo._get_or_create_watermarks(instance, [GeoDirection.SOURCE]))[
            GeoDirection.SOURCE
        ]
        assert wm.collection_metadata["failure_code"] == (
            "geo.property.missing" if readable else "geo.property.unreadable"
        )
    else:
        assert h_report.metrics_state == "OK" and h_report.searches_sent == 2
        assert g_report.searches_sent == 1 and not g_report.partial_failure
    assert provider.property_calls == []
    await db_session.commit()
    factory = Mock(side_effect=AssertionError("doctor must not build a provider"))
    doctor = Doctor(settings=settings, sessionmaker=async_sessionmaker(
        db_session.bind, expire_on_commit=False), provider_factory=factory, now=lambda: NOW)
    result = await doctor.check_geo_country_source()
    expected_status = Status.FAIL if not readable else Status.WARN if missing else Status.PASS
    assert result.status == expected_status
    expected = "STALE" if state == "COMPLETE" and stamp < NOW else state or "NEVER"
    if state == "REFRESHING" and stamp is None:
        expected = "NEVER"
    line = render_text([result])
    assert f"snapshot {expected}" in line
    assert f"age {int((NOW - stamp).total_seconds())} s" in line if stamp else "age unknown" in line
    factory.assert_not_called()


async def test_one_query_for_two_and_twenty_names_exact_matching_and_console_isolation(db_session):
    instance = await catalog_instance(db_session)
    other = await make_instance(db_session)
    for offset in range(5, 12000, 1000):
        await db_session.execute(insert(PropertyCatalogEntry), [{
            "instance_id": instance.id, "qradar_id": i, "identifier": f"id{i}",
            "name": f"Other {i}", "property_type": "string", "name_allowed": True,
            "type_ids": [], "use_for_rule_engine": False, "auto_discovered": False,
        } for i in range(offset, min(offset + 1000, 12000))])
    await db_session.commit()
    counts = []
    for size in (2, 20):
        names = ["Value", "absent", *[f"Name {i}" for i in range(size - 2)]]
        started = time.perf_counter()
        with recorded_statements(db_session) as statements:
            result = await stored_property_names(db_session, instance_id=instance.id, names=names,
                                                 now=NOW, refresh_seconds=600)
        counts.append(len(statements))
        print(f"SCALE2c 12000-entry snapshot, {size} names: {len(statements)} statement; "
              f"{time.perf_counter() - started:.6f} s")
        assert result.present == {name: name == "Value" for name in names}
    assert counts == [1, 1]
    exact = await stored_property_names(db_session, instance_id=instance.id,
                                        names=["value", "Val%", "Value", "Value "],
                                        now=NOW, refresh_seconds=600)
    assert exact.present == {"value": False, "Val%": False, "Value": True, "Value ": False}
    isolated = await stored_property_names(db_session, instance_id=other.id, names=NAMES,
                                           now=NOW, refresh_seconds=600)
    assert not isolated.readable and isolated.present == dict.fromkeys(NAMES)
    await db_session.execute(delete(PropertyCatalogEntry))
    empty = await stored_property_names(db_session, instance_id=instance.id, names=NAMES,
                                       now=NOW, refresh_seconds=600)
    assert empty.readable and empty.present == dict.fromkeys(NAMES, False)


async def test_database_read_failure_is_closed_and_does_not_poison_the_run(db_session, monkeypatch):
    from app.collectors import geo_collector, qradar_health_collector
    from app.services import property_catalog

    instance = await catalog_instance(db_session)
    await db_session.commit()
    settings = get_settings().model_copy(update={"geo_country_source": "property",
                                                 "geo_directions": ["SOURCE"]})

    async def rejected(session, **kwargs):
        await session.execute(text("SELECT * FROM scale2c_absent_relation"))

    monkeypatch.setattr(qradar_health_collector, "stored_property_names", rejected)
    monkeypatch.setattr(geo_collector, "stored_property_names", rejected)
    provider = NoPropertyRequests(seed=1, now=NOW)
    health = QRadarHealthCollector(db_session, provider, settings=settings, clock=lambda: NOW,
                                   health_provider_factory=lambda _: NoPropertyRequests())
    geo = GeoCollector(db_session, provider, settings=settings, clock=lambda: NOW)
    assert (await health.collect(instance)).searches_sent == 1
    assert (await geo.collect(instance)).searches_sent == 0
    run = await db_session.scalar(select(QRadarHealthRun))
    assert run.metrics_reason_code == "qradar_health_detail.reason.property_unreadable"
    assert run.notifications_state == "OK"
    assert await db_session.scalar(select(func.count()).select_from(QRadarHealthMetricBucket)) == 0
    assert await db_session.scalar(select(func.count()).select_from(GeoCountryBucket)) == 0
    assert provider.property_calls == []
    await db_session.commit()

    async def private_failure(*args, **kwargs):
        raise RuntimeError("token=planted-secret")

    monkeypatch.setattr(property_catalog, "stored_property_names", private_failure)
    doctor = Doctor(settings=settings, sessionmaker=async_sessionmaker(db_session.bind),
                    now=lambda: NOW)
    result = await doctor.check_geo_country_source()
    assert result.status is Status.FAIL and "RuntimeError" in result.message
    assert "planted-secret" not in result.message
