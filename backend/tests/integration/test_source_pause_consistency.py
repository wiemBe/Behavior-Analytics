"""D-52 active Phase A population, legacy SQL and retained investigation history."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256

import pytest
from sqlalchemy import event, null, text, update

from app.api.routes.outage_policy import _governed_counts
from app.collectors.log_source_collector import LogSourceCollector
from app.core.config import Settings
from app.fleet.summary import build_fleet_summary
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyState,
    AnomalyType,
    OutagePolicyMode,
    OutagePolicyScope,
    SourceClass,
)
from app.models.log_source import LogSource
from app.models.outage import OutageAlertPolicy
from app.repositories.anomaly import AnomalyRepository
from app.repositories.log_source import LogSourceRepository
from app.schemas.anomaly import SubjectKind
from app.services.overview import OverviewService
from tests.integration.factories import make_instance, make_log_source, utc
from tests.integration.test_anomaly_histogram import endpoint, phase_a, waf
from tests.integration.test_inventory_source_pauses import SourceProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = utc(2026, 11, 21, 12)
DETECTED = NOW - timedelta(hours=1)
WINDOW = {"since": DETECTED, "until": NOW}

# SHA256 of the exact before_cursor_execute SQL bytes, recorded on the unmodified
# P10a base. Parameters are deliberately separate: UUIDs differ between test runs.
LEGACY_SQL = {
    "counts": [
        "b90c33721b8e26a19bef2d462231afb8cd23255b9af06dd89764eac73c8cd53c"
    ],
    "page_counts": [
        "388f5afb19e37043c094aba1ddfb850429b8be36cccb7952111181c163d49133"
    ],
    "open": [
        "f0163723b7c45d899bbbda7e3527faa620f8509dd9777e10ae2ededfdad82d92"
    ],
    "anomalous": [
        "f45ca11a97f7fda1b1c6c4215e1d0322c10d6de4f86ffd79fb566fdd7b19d8aa"
    ],
    "unified": [
        "0cfd43230108ccc9aa6ad4d5f95074a28a1d3965b86155682a3b1a719fd27137",
        "aa8703539f8f196552285268356318538be63608a964575db187fc19517adb40"
    ],
    "families": [
        "8208be495978a6c497d1eef9a08e20918940e1e978f29650786058541b9d9693"
    ]
}


@contextmanager
def _record(session):
    statements = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.fixture
async def fleet(db_session):
    session = db_session
    instance = await make_instance(session)
    sources = {}
    incidents = {}
    for qid, (name, enabled) in enumerate(
        [("enabled", True), ("legacy", False), ("unknown", True),
         ("history", False), ("edge", True)], 1
    ):
        source = await make_log_source(
            session, instance, qradar_id=qid, name=name, enabled=enabled,
            monitoring_enabled=True, first_seen_at=NOW - timedelta(days=2),
        )
        sources[name] = source
        incident = phase_a(
            source.id, DETECTED, anomaly_type=AnomalyType.NO_EVENTS,
            state=AnomalyState.RESOLVED if name == "history" else AnomalyState.OPEN,
            resolved_at=DETECTED if name == "history" else None,
        )
        session.add(incident)
        incidents[name] = incident
    ep = endpoint(instance.id, sources["legacy"].id)
    session.add(ep)
    await session.flush()
    waf_incident = waf(ep.id, DETECTED)
    session.add(waf_incident)
    # Production's column is NOT NULL. Exercise defensive SQL NULL semantics in
    # this disposable schema only, without changing metadata or any migration.
    await session.execute(text("ALTER TABLE log_source ALTER COLUMN enabled DROP NOT NULL"))
    await session.execute(update(LogSource).where(
        LogSource.id == sources["unknown"].id
    ).values(enabled=null()))
    await session.commit()
    return instance, sources, incidents, waf_incident


def _calls(session, sources, **policy):
    ls = LogSourceRepository(session)
    anomalies = AnomalyRepository(session)
    ids = [source.id for source in sources.values()]
    return {
        "counts": lambda: ls.open_anomaly_counts(**policy),
        "page_counts": lambda: ls.open_anomaly_counts(ids, **policy),
        "open": lambda: ls.open_anomalies_for(sources["legacy"].id, **policy),
        "anomalous": lambda: ls.anomalous_source_count(**policy),
        "unified": lambda: anomalies.list_unified(
            limit=50, offset=0, active_only=True, **WINDOW, **policy,
        ),
        "families": lambda: anomalies.active_family_counts(**WINDOW, **policy),
    }


async def _assert_active(session, sources, *, enabled_names):
    ls = LogSourceRepository(session)
    anomalies = AnomalyRepository(session)
    expected = {sources[name].id: 1 for name in enabled_names}
    assert await ls.open_anomaly_counts(source_pause=True) == expected
    assert await ls.open_anomaly_counts(list(expected), source_pause=True) == expected
    assert await ls.open_anomaly_counts([], source_pause=True) == {}
    assert await ls.anomalous_source_count(source_pause=True) == len(expected)
    for name, source in sources.items():
        rows = await ls.open_anomalies_for(source.id, source_pause=True)
        assert len(rows) == int(name in enabled_names), name
    rows, total = await anomalies.list_unified(
        limit=50, offset=0, active_only=True, source_pause=True, **WINDOW,
    )
    assert {r["log_source_id"] for r in rows if r["subject_kind"] == "LOG_SOURCE"} == set(expected)
    assert total == len(expected) + 1  # WAF on the legacy disabled source survives.
    families = await anomalies.active_family_counts(source_pause=True, **WINDOW)
    assert families[("log_source", "OPEN")] == len(expected)
    assert sum(families.values()) == total


async def _existing_populations(session, *, active, disabled, incidents):
    overview = await OverviewService(session).build()
    assert overview.log_sources.silent_log_sources == active
    # The inventory monitored census intentionally includes every stored flag.
    assert overview.log_sources.monitored_log_sources == 5
    assert overview.log_sources.total_log_sources == 5
    summary = await build_fleet_summary(
        session, window="1h", now=NOW, bucket_seconds=300,
    )
    cls = next(c for c in summary.classes if c.source_class == SourceClass.UNCLASSIFIED)
    assert cls.sources.enabled == cls.monitored_sources == active
    assert cls.sources.disabled == disabled
    assert cls.buckets.expected == active * 12
    # This existing tally includes legacy disabled/unknown incidents.
    assert cls.open_incidents == {AnomalyType.NO_EVENTS.value: incidents}
    policy = OutageAlertPolicy(
        scope_kind=OutagePolicyScope.CLASS, scope_value=SourceClass.UNCLASSIFIED.value,
        mode=OutagePolicyMode.ALERT,
    )
    governed = await _governed_counts(session, [policy], OutagePolicyMode.ALERT)
    assert governed == {SourceClass.UNCLASSIFIED.value: active}


async def test_sync_overview_outage_fleet_source_counts_active_list_agree(db_session, fleet):
    instance, sources, incidents, _ = fleet
    await _assert_active(db_session, sources, enabled_names={"enabled", "edge"})
    await _existing_populations(db_session, active=2, disabled=2, incidents=4)
    settings = Settings(
        _env_file=None, qradar_source_pause_enabled=True,
        inventory_new_source_alerts_enabled=False,
    )
    result = await LogSourceCollector(
        db_session, SourceProvider([(sources["edge"].qradar_id, False)]),
        settings=settings, clock=lambda: NOW,
    ).sync(instance)
    assert result.status == "COMPLETE"
    await db_session.commit()
    await db_session.refresh(sources["edge"])
    await db_session.refresh(incidents["edge"])
    assert sources["edge"].enabled is False
    assert incidents["edge"].state == AnomalyState.RESOLVED
    assert incidents["edge"].resolved_at == NOW
    await _assert_active(db_session, sources, enabled_names={"enabled"})
    await _existing_populations(db_session, active=1, disabled=3, incidents=3)
    # P10b must wire this current caller; it still uses the default-off count.
    overview = await OverviewService(db_session).build()
    assert overview.log_sources.anomalous_log_sources == 3


@pytest.mark.parametrize("state", [None, AnomalyState.OPEN, AnomalyState.RESOLVED])
async def test_closed_history_detail_retained(db_session, fleet, state):
    _, sources, incidents, _ = fleet
    repo = AnomalyRepository(db_session)
    with _record(db_session) as old_sql:
        old_rows, old_total = await repo.list_unified(
            limit=50, offset=0, active_only=False, state=state, **WINDOW,
        )
    with _record(db_session) as new_sql:
        rows, total = await repo.list_unified(
            limit=50, offset=0, active_only=False, state=state, source_pause=True, **WINDOW,
        )
    assert (rows, total) == (old_rows, old_total)
    assert new_sql == old_sql
    expected = 6 if state is None else 5 if state == AnomalyState.OPEN else 1
    assert total == expected
    for name in ("legacy", "unknown", "history"):
        assert (await repo.get_detail(incidents[name].id)).id == incidents[name].id
        assert (await LogSourceRepository(db_session).get(sources[name].id)).id == sources[name].id


async def test_only_phase_a_active_counts_filtered(db_session, fleet):
    _, sources, _, waf_incident = fleet
    repo = AnomalyRepository(db_session)
    rows, total = await repo.list_unified(
        limit=50, offset=0, active_only=True, source_pause=True,
        log_source_id=sources["legacy"].id, **WINDOW,
    )
    assert total == 1 and [r["id"] for r in rows] == [waf_incident.id]
    with _record(db_session) as off:
        old = await repo.list_unified(
            limit=50, offset=0, active_only=True, subject_kind=SubjectKind.WAF_ENDPOINT, **WINDOW,
        )
    with _record(db_session) as on:
        new = await repo.list_unified(
            limit=50, offset=0, active_only=True, subject_kind=SubjectKind.WAF_ENDPOINT,
            source_pause=True, **WINDOW,
        )
    assert new == old and on == off
    families = await repo.active_family_counts(source_pause=True, **WINDOW)
    assert families[("waf", "OPEN")] == 1
    assert sum(families.values()) == 3


async def test_switch_off_old_counts(db_session, fleet):
    _, sources, _, _ = fleet
    expected_ids = {sources[name].id for name in ("enabled", "legacy", "unknown", "edge")}
    results = {}
    for name, call in _calls(db_session, sources).items():
        with _record(db_session) as original:
            results[name] = await call()
        with _record(db_session) as explicit:
            result = await _calls(db_session, sources, source_pause=False)[name]()
        assert result == results[name]
        assert explicit == original
        assert [sha256(sql.encode()).hexdigest() for sql in explicit] == LEGACY_SQL[name]
    assert results["counts"] == results["page_counts"] == dict.fromkeys(expected_ids, 1)
    assert len(results["open"]) == 1
    assert results["anomalous"] == 4
    assert results["unified"][1] == sum(results["families"].values()) == 5
    assert results["families"] == {("log_source", "OPEN"): 4, ("waf", "OPEN"): 1}


async def test_statement_counts_unchanged(db_session, fleet):
    _, sources, _, _ = fleet
    for name, call in _calls(db_session, sources, source_pause=False).items():
        with _record(db_session) as off:
            await call()
        with _record(db_session) as on:
            await _calls(db_session, sources, source_pause=True)[name]()
        assert len(on) == len(off) == (2 if name == "unified" else 1), name


@pytest.mark.parametrize("state", list(ACTIVE_ANOMALY_STATES))
async def test_active_states_suppression_and_window(db_session, fleet, state):
    instance, sources, incidents, _ = fleet
    for name in ("enabled", "legacy", "unknown", "edge"):
        incidents[name].state = state
        incidents[name].suppressed = True
    await db_session.commit()
    ls = LogSourceRepository(db_session)
    repo = AnomalyRepository(db_session)
    assert await ls.open_anomaly_counts(source_pause=True) == {}
    assert await ls.anomalous_source_count(source_pause=True) == 0
    assert len(await ls.open_anomalies_for(sources["enabled"].id, source_pause=True)) == 1
    assert await ls.open_anomalies_for(sources["unknown"].id, source_pause=True) == []
    rows, total = await repo.list_unified(
        limit=1, offset=1, active_only=True, source_pause=True, **WINDOW,
    )
    assert len(rows) == 1 and total == 3  # Unified retains suppressed incidents.
    families = await repo.active_family_counts(source_pause=True, **WINDOW)
    assert families[("log_source", state.value)] == 2
    assert sum(families.values()) == total
    selected, selected_total = await repo.list_unified(
        limit=50, offset=0, active_only=True, source_pause=True, instance_id=instance.id,
        log_source_id=sources["enabled"].id, anomaly_type=AnomalyType.NO_EVENTS.value,
        state=state, **WINDOW,
    )
    assert selected_total == 1 and selected[0]["id"] == incidents["enabled"].id
    for bounds in ({"since": None, "until": NOW},
                   {"since": None, "until": DETECTED},
                   {"since": DETECTED + timedelta(seconds=1), "until": NOW}):
        _, listed = await repo.list_unified(
            limit=50, offset=0, active_only=True, source_pause=True, **bounds,
        )
        counts = await repo.active_family_counts(source_pause=True, **bounds)
        assert sum(counts.values()) == listed == (3 if bounds["until"] == NOW
                                                and bounds["since"] is None else 0)
