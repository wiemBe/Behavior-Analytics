"""D-52 policy reaches HTTP active reads from the runtime feature-switch table."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import event, null, text, update

from app.models.enums import ACTIVE_ANOMALY_STATES, AnomalyState, AnomalyType, EvidenceStatus
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource
from app.repositories.anomaly import AnomalyRepository
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_anomaly_histogram import endpoint, phase_a, waf
from tests.integration.test_fleet_api import EDITOR, Client, run
from tests.integration.test_source_pause_consistency import _record

pytestmark = pytest.mark.integration
ROOT = "/api/v1"
SWITCH = "qradar_source_pause_enabled"
# Exact before_cursor_execute SQL from the unmodified P10b base. On a deliberate
# model/SQLAlchemy change, regenerate these hashes against the unmodified legacy code.
LEGACY_SQL = {
    "state_counts": ["fe827d470a464acfadb5a07f28048819d352d7b74de7ce1830b46315b7a3390e"],
    "highest_deviation": ["47171d71800891d60f2e980fe56ca76cc19c7f0ed4eb0c547e26b3b549113707"],
}


async def _world(session):
    now = datetime.now(UTC) - timedelta(hours=1)
    instance = await make_instance(session)
    sources, rows = {}, {}
    for qid, name in enumerate(("enabled", "disabled", "unknown"), 1):
        source = await make_log_source(
            session, instance, qradar_id=qid, name=name, enabled=name == "enabled",
            monitoring_enabled=True,
        )
        sources[name] = source.id
        for state, kind in zip(ACTIVE_ANOMALY_STATES, (AnomalyType.VOLUME_SPIKE,
                                    AnomalyType.VOLUME_DROP, AnomalyType.NO_EVENTS), strict=True):
            row = phase_a(
                source.id, now, state=state, anomaly_type=kind,
                deviation_ratio=2 if name == "enabled" else 99,
                evidence_status=EvidenceStatus.PENDING,
            )
            session.add(row)
            await session.flush()
            rows[name, state.value] = row.id
        resolved = phase_a(source.id, now, state=AnomalyState.RESOLVED, resolved_at=now)
        session.add(resolved)
        await session.flush()
        rows[name, "RESOLVED"] = resolved.id
    ep = endpoint(instance.id, sources["disabled"])
    session.add(ep)
    await session.flush()
    finding = waf(ep.id, now)
    session.add(finding)
    await session.flush()
    return sources, rows, finding.id


@pytest.fixture
def world(db_schema):
    return run(_world)


def _switch(enabled):
    async def write(session):
        row = await session.get(FeatureSwitch, SWITCH)
        if row is None:
            session.add(FeatureSwitch(
                key=SWITCH, enabled=enabled, updated_at=datetime.now(UTC), updated_by="test",
            ))
        else:
            row.enabled = enabled
    run(write)


def _json(client, path, **kwargs):
    response = client.get(ROOT + path, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_routes(client, world, *, on):
    sources, rows, waf_id = world
    selected = {"enabled"} if on else set(sources)
    page = _json(client, "/log-sources")
    assert page["total"] == 3  # Inventory itself remains visible.
    assert {item["id"]: item["open_anomaly_count"] for item in page["items"]} == {
        str(sid): 3 if name in selected else 0 for name, sid in sources.items()
    }
    for name, sid in sources.items():
        detail = _json(client, f"/log-sources/{sid}")
        assert detail["id"] == str(sid)
        assert detail["open_anomaly_count"] == (3 if name in selected else 0)
        opened = _json(client, f"/log-sources/{sid}/anomalies")
        assert {r["id"] for r in opened} == {
            str(rows[name, state.value]) for state in ACTIVE_ANOMALY_STATES
            if name in selected
        }
        patched = client.patch(ROOT + f"/log-sources/{sid}", json={"owner": "reviewed"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["open_anomaly_count"] == detail["open_anomaly_count"]
    expected = {str(rows[name, state.value]) for name in selected
                for state in ACTIVE_ANOMALY_STATES} | {str(waf_id)}
    listed = _json(client, "/anomalies", params={"active_only": "true", "limit": 200})
    assert {r["id"] for r in listed["items"]} == expected
    assert listed["total"] == len(expected)
    response = client.get(ROOT + "/anomalies.csv", params={"active_only": "true"})
    assert response.status_code == 200, response.text
    exported = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
    assert {r["id"] for r in exported} == expected
    summary = _json(client, "/anomalies/summary")
    count = len(selected)
    assert {key: summary[key] for key in (
        "open_anomalies", "spikes", "drops", "silent_sources", "candidates", "recovering",
    )} == dict(zip(("open_anomalies", "spikes", "drops", "silent_sources", "candidates",
                    "recovering"), (count * 3, count, count, count, count, count), strict=True))
    families = summary["active_by_family"]
    assert families["log_source"] == {
        "open": count, "candidate": count, "recovering": count, "total": count * 3,
    }
    assert families["log_source"]["total"] == summary["open_anomalies"]
    assert families["waf"]["total"] == 1
    assert sum(f["total"] for f in families.values()) == listed["total"]
    assert {r["id"] for r in summary["highest_deviation"]} == expected - {str(waf_id)}
    assert summary["evidence_pending"] == 9  # All-time census, including disabled sources.
    overview = _json(client, "/overview")["log_sources"]
    assert overview["anomalous_log_sources"] == count
    assert overview["monitored_log_sources"] == 3


def test_db_switch_reaches_source_list_detail_and_unified_counts(world):
    client = Client(EDITOR)
    _switch(True)
    _assert_routes(client, world, on=True)


def test_default_off_actual_routes_unchanged(world):
    client = Client(EDITOR)
    _assert_routes(client, world, on=False)
    _switch(False)
    _assert_routes(client, world, on=False)


def test_table_flip_back_off_restores_next_request(world):
    client = Client(EDITOR)
    _switch(True)
    _assert_routes(client, world, on=True)
    _switch(False)
    _assert_routes(client, world, on=False)


@pytest.mark.parametrize("state", [None, "OPEN", "RESOLVED"])
def test_history_read_still_visible(world, state):
    sources, rows, waf_id = world
    _switch(True)
    client = Client(EDITOR)
    params = {"active_only": "false", "log_source_id": str(sources["disabled"])}
    if state:
        params["state"] = state
    expected = {str(rid) for (name, row_state), rid in rows.items()
                if name == "disabled" and (state is None or row_state == state)}
    if state is None or state == "OPEN":
        expected.add(str(waf_id))
    listed = _json(client, "/anomalies", params=params)
    assert {r["id"] for r in listed["items"]} == expected
    response = client.get(ROOT + "/anomalies.csv", params=params)
    assert response.status_code == 200, response.text
    exported = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
    assert {r["id"] for r in exported} == expected
    for (name, _), rid in rows.items():
        if name == "disabled":
            assert _json(client, f"/anomalies/{rid}")["id"] == str(rid)
    assert _json(client, f"/log-sources/{sources['disabled']}")["enabled"] is False


def test_detail_count_is_bounded_to_requested_id(world):
    sources, _, _ = world
    _switch(True)
    client = Client(EDITOR)
    statements = []

    def record(conn, cursor, sql, parameters, *args):
        if "GROUP BY log_source_anomaly.log_source_id" in sql:
            statements.append((sql, parameters))

    event.listen(client.engine.sync_engine, "before_cursor_execute", record)
    try:
        sid = sources["disabled"]
        _json(client, f"/log-sources/{sid}")
        response = client.patch(ROOT + f"/log-sources/{sid}", json={"owner": "bounded"})
        assert response.status_code == 200, response.text
    finally:
        event.remove(client.engine.sync_engine, "before_cursor_execute", record)
    assert len(statements) == 2
    for sql, parameters in statements:
        assert "log_source_anomaly.log_source_id IN (" in sql
        assert sid in parameters
        assert "log_source.enabled IS true" in sql


def test_summary_queries_legacy_sql_and_active_only_policy(world):
    async def check(session):
        # Defensive NULL handling only in the disposable schema; production is NOT NULL.
        await session.execute(text("ALTER TABLE log_source ALTER COLUMN enabled DROP NOT NULL"))
        await session.execute(update(LogSource).where(
            LogSource.id == world[0]["unknown"]
        ).values(enabled=null()))
        repo = AnomalyRepository(session)
        captured = {}
        for name in ("state_counts", "highest_deviation"):
            method = getattr(repo, name)
            with _record(session) as original:
                old = await method()
            captured[name] = [sha256(sql.encode()).hexdigest() for sql in original]
            with _record(session) as explicit:
                assert await method(source_pause=False) == old
            assert original == explicit
            assert captured[name] == LEGACY_SQL[name]
            with _record(session) as filtered:
                result = await method(source_pause=True)
            assert len(original) == len(filtered) == 1
            assert "JOIN log_source ON" in filtered[0]
            assert "log_source.enabled IS true" in filtered[0]
            if name == "state_counts":
                assert sum(c for (state, _), c in result.items() if state == "RESOLVED") == 3
                assert sum(result.values()) == 6
            else:
                assert len(result) == 3
                assert {row.log_source_id for row in result} == {world[0]["enabled"]}
    run(check)
