"""SOC-17d suppression routes: access, validation, audit and read models."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    IdentityAnomalyType,
    IdentityMeasurement,
    Severity,
)
from app.models.identity_anomaly import IdentityAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import (
    MISSING_ID,
    NOTHING,
    VIEWER,
    Client,
    audit_rows,
    run,
)

pytestmark = pytest.mark.integration

ANALYST = Principal(subject="analyst", permissions=frozenset({"read:*", "alert:suppress"}))
ALERT_WRITER = Principal(
    subject="alert-writer", permissions=frozenset({"read:alerts", "alert:suppress"})
)
BASE = "/api/v1"
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def _body(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"days": 1, "reason": "operator suppression"}
    values.update(overrides)
    return values


def _alert(
    *,
    source_id: uuid.UUID | None,
    anomaly_id: uuid.UUID | None,
    source_type: str = "log_source",
    opened_at: datetime = NOW,
    title: str = "a stored alert",
) -> Alert:
    fingerprint = f"test:{uuid.uuid4()}"
    return Alert(
        dedup_key=fingerprint,
        fingerprint=fingerprint,
        title=title,
        severity=Severity.HIGH.value,
        source_type=source_type,
        source_id=source_id,
        context={},
        opened_at=opened_at,
        first_seen_at=opened_at,
        evidence_snapshot={},
        source_anomaly_ids=[str(anomaly_id)] if anomaly_id is not None else [],
    )


async def _phase_objects(session):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    source = await make_log_source(session, instance, qradar_id=17, name="edge-01")
    anomaly = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=AnomalyType.VOLUME_SPIKE.value,
        severity=Severity.HIGH.value,
        detected_at=NOW,
        state=AnomalyState.OPEN.value,
        opened_at=NOW,
    )
    session.add(anomaly)
    await session.flush()
    alert = _alert(
        source_id=source.id,
        anomaly_id=anomaly.id,
        title="NO_EVENTS in the title is not the condition",
    )
    session.add(alert)
    await session.flush()
    return {
        "instance_id": str(instance.id),
        "source_id": str(source.id),
        "anomaly_id": str(anomaly.id),
        "alert_id": str(alert.id),
    }


def _setup_phase():  # type: ignore[no-untyped-def]
    return run(_phase_objects)


def _suppression_row(
    subject_id: str,
    detector: str | None,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    ended_at: datetime | None = None,
    reason: str = "stored operator note",
) -> AlertSuppression:
    now = datetime.now(UTC)
    return AlertSuppression(
        family="log_source",
        subject_id=uuid.UUID(subject_id),
        detector_type=detector,
        kind="ANALYST",
        reason=reason,
        owner="shift-lead",
        starts_at=starts_at or now - timedelta(hours=1),
        ends_at=ends_at or now + timedelta(days=1),
        ended_at=ended_at,
        ended_by="shift-lead" if ended_at is not None else None,
        created_by="analyst",
    )


def test_create_extend_end_derives_condition_and_audits_without_reason(
    db_schema, caplog: pytest.LogCaptureFixture
) -> None:
    objects = _setup_phase()
    client = Client(ANALYST)
    secret_reason = "sensitive analyst note: do not put this in audit or logs"
    caplog.set_level(logging.INFO)

    created = client.post(
        f"{BASE}/alerts/{objects['alert_id']}/suppress",
        json=_body(reason=secret_reason, owner="shift-lead"),
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["family"] == "log_source"
    assert body["subject_id"] == objects["source_id"]
    assert body["detector_type"] == AnomalyType.VOLUME_SPIKE.value
    assert body["subject_name"] == "edge-01"
    assert body["owner"] == "shift-lead" and body["hit_count"] == 0

    extended = client.post(
        f"{BASE}/anomalies/log_source/{objects['anomaly_id']}/suppress",
        json=_body(days=7, reason="replacement reason"),
    )
    assert extended.status_code == 200, extended.text
    assert extended.json()["id"] == body["id"]
    assert extended.json()["owner"] == "analyst"
    assert extended.json()["reason"] == "replacement reason"

    ended = client.post(f"{BASE}/suppressions/{body['id']}/end")
    assert ended.status_code == 200, ended.text
    assert ended.json()["ended_at"] is not None
    assert ended.json()["ended_by"] == "analyst"
    assert client.get(f"{BASE}/suppressions?active=true").json() == []
    historical = client.get(f"{BASE}/suppressions?active=false").json()
    assert len(historical) == 1 and historical[0]["id"] == body["id"]

    actions = [name for name in (
        "alert_suppression.create", "alert_suppression.extend", "alert_suppression.end"
    ) for _ in audit_rows(name)]
    assert actions == [
        "alert_suppression.create", "alert_suppression.extend", "alert_suppression.end"
    ]
    create = audit_rows("alert_suppression.create")[0]
    extend = audit_rows("alert_suppression.extend")[0]
    end = audit_rows("alert_suppression.end")[0]
    assert create.object_id.endswith(f":{AnomalyType.VOLUME_SPIKE.value}")
    assert create.detail["before"] is None
    assert extend.detail["before"]["owner"] == "shift-lead"
    assert extend.detail["after"]["owner"] == "analyst"
    assert end.detail["before"]["ended_at"] is None
    assert end.detail["after"]["ended_by"] == "analyst"
    for row in (create, extend, end):
        assert secret_reason not in json.dumps(row.detail)
        assert "replacement reason" not in json.dumps(row.detail)
    assert secret_reason not in caplog.text
    assert "replacement reason" not in caplog.text


def test_validation_writes_and_audits_nothing(db_schema) -> None:
    objects = _setup_phase()
    client = Client(ANALYST)
    url = f"{BASE}/anomalies/log_source/{objects['anomaly_id']}/suppress"
    invalid = [
        _body(days=2),
        _body(days=0),
        _body(days=31),
        _body(days="forever"),
        _body(reason="xx"),
        _body(reason="x" * 1001),
        {"days": 1},
        _body(owner=""),
        _body(kind="KNOWN_CHANGE"),
        _body(reference="CHG-not-analyst"),
        _body(detector_type="NO_EVENTS"),
    ]
    for payload in invalid:
        response = client.post(url, json=payload)
        assert response.status_code == 422, (payload, response.text)

    async def pw_alert(session):  # type: ignore[no-untyped-def]
        alert = _alert(source_id=None, anomaly_id=None, source_type="pw_value")
        session.add(alert)
        await session.flush()
        return str(alert.id)

    pw_alert_id = run(pw_alert)
    assert client.post(
        f"{BASE}/alerts/{pw_alert_id}/suppress", json=_body(whole_source=True)
    ).status_code == 422
    assert client.get(f"{BASE}/suppressions?active=false").json() == []
    for action in ("create", "extend", "end"):
        assert audit_rows(f"alert_suppression.{action}") == []


def test_known_change_and_whole_source_are_server_scoped(db_schema) -> None:
    objects = _setup_phase()
    response = Client(ANALYST).post(
        f"{BASE}/anomalies/log_source/{objects['anomaly_id']}/suppress",
        json=_body(
            days=30,
            whole_source=True,
            kind="KNOWN_CHANGE",
            reference="CHG-2026-17",
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detector_type"] is None
    assert body["kind"] == "KNOWN_CHANGE" and body["reference"] == "CHG-2026-17"
    # Owner was omitted: the server names the caller, it does not accept a blank.
    assert body["owner"] == "analyst"


def test_an_expired_open_row_is_closed_before_the_new_create(db_schema) -> None:
    objects = _setup_phase()

    async def expired(session):  # type: ignore[no-untyped-def]
        row = _suppression_row(
            objects["source_id"],
            AnomalyType.VOLUME_SPIKE.value,
            starts_at=datetime.now(UTC) - timedelta(days=2),
            ends_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(row)
        await session.flush()
        return str(row.id)

    old_id = run(expired)
    response = Client(ANALYST).post(
        f"{BASE}/anomalies/log_source/{objects['anomaly_id']}/suppress", json=_body()
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] != old_id

    async def rows(session):  # type: ignore[no-untyped-def]
        old = await session.get(AlertSuppression, uuid.UUID(old_id))
        assert old is not None and old.ended_at == old.ends_at
        return int(await session.scalar(select(func.count()).select_from(AlertSuppression)) or 0)

    assert run(rows) == 2
    [audit] = audit_rows("alert_suppression.create")
    assert audit.detail["closed_expired"]["ended_at"] is not None
    assert "operator suppression" not in json.dumps(audit.detail)


def test_an_extend_cannot_stretch_one_row_past_thirty_days_from_its_start(
    db_schema,
) -> None:
    objects = _setup_phase()
    client = Client(ANALYST)
    created = client.post(
        f"{BASE}/anomalies/log_source/{objects['anomaly_id']}/suppress", json=_body(days=1)
    )
    assert created.status_code == 200, created.text
    suppression_id = created.json()["id"]

    async def age(session):  # type: ignore[no-untyped-def]
        row = await session.get(AlertSuppression, uuid.UUID(suppression_id))
        assert row is not None
        row.starts_at = datetime.now(UTC) - timedelta(days=2)
        await session.flush()
        return row.ends_at

    original_end = run(age)
    refused = client.post(
        f"{BASE}/anomalies/log_source/{objects['anomaly_id']}/suppress", json=_body(days=30)
    )
    assert refused.status_code == 422, refused.text

    async def unchanged(session):  # type: ignore[no-untyped-def]
        row = await session.get(AlertSuppression, uuid.UUID(suppression_id))
        assert row is not None and row.ended_at is None
        return row.ends_at

    assert run(unchanged) == original_end
    assert audit_rows("alert_suppression.extend") == []


def test_access_requires_the_object_read_and_alert_suppress_permissions(db_schema) -> None:
    viewer = Client(VIEWER)
    assert viewer.get(f"{BASE}/suppressions").status_code == 200
    assert viewer.get(f"{BASE}/suppressions/for/log_source/{MISSING_ID}").status_code == 404
    for method, url, payload in (
        ("POST", f"{BASE}/alerts/{MISSING_ID}/suppress", _body()),
        ("POST", f"{BASE}/anomalies/log_source/{MISSING_ID}/suppress", _body()),
        ("POST", f"{BASE}/suppressions/{MISSING_ID}/end", None),
    ):
        assert viewer.request(method, url, json=payload).status_code == 403
        assert Client(NOTHING).request(method, url, json=payload).status_code == 403
    # alert:suppress and read:alerts are not the anomaly surface's read permission.
    assert Client(ALERT_WRITER).post(
        f"{BASE}/anomalies/log_source/{MISSING_ID}/suppress", json=_body()
    ).status_code == 403
    # But the same principal can reach the alert object lookup.
    assert Client(ALERT_WRITER).post(
        f"{BASE}/alerts/{MISSING_ID}/suppress", json=_body()
    ).status_code == 404


def test_without_a_token_the_new_routes_are_401(db_schema) -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": "oidc"})

    async def no_session():  # type: ignore[no-untyped-def]
        yield None

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = no_session
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(f"{BASE}/suppressions").status_code == 401
    assert client.post(
        f"{BASE}/alerts/{MISSING_ID}/suppress", json=_body()
    ).status_code == 401
    assert client.post(
        f"{BASE}/anomalies/log_source/{MISSING_ID}/suppress", json=_body()
    ).status_code == 401
    assert client.post(f"{BASE}/suppressions/{MISSING_ID}/end").status_code == 401
    assert client.get(f"{BASE}/suppressions/for/log_source/{MISSING_ID}").status_code == 401


def test_missing_objects_are_404_and_an_unknown_family_is_422(db_schema) -> None:
    client = Client(ANALYST)
    assert client.post(
        f"{BASE}/alerts/{MISSING_ID}/suppress", json=_body()
    ).status_code == 404
    assert client.post(
        f"{BASE}/anomalies/log_source/{MISSING_ID}/suppress", json=_body()
    ).status_code == 404
    assert client.post(
        f"{BASE}/anomalies/made_up/{MISSING_ID}/suppress", json=_body()
    ).status_code == 422
    assert client.post(f"{BASE}/suppressions/{MISSING_ID}/end").status_code == 404
    assert client.get(
        f"{BASE}/suppressions/for/log_source/{MISSING_ID}"
    ).status_code == 404


def test_list_hit_counts_maintenance_and_activity(db_schema) -> None:
    objects = _setup_phase()

    async def fixture(session):  # type: ignore[no-untyped-def]
        from app.models.log_source import LogSource

        source = await session.get(LogSource, uuid.UUID(objects["source_id"]))
        source.maintenance_mode = True
        source.maintenance_until = datetime.now(UTC) + timedelta(hours=2)
        source.maintenance_reason = "maintenance note"
        narrow = _suppression_row(objects["source_id"], AnomalyType.VOLUME_SPIKE.value)
        wide = _suppression_row(objects["source_id"], None)
        ended = _suppression_row(
            objects["source_id"], AnomalyType.VOLUME_DROP.value, ended_at=NOW
        )
        future = _suppression_row(
            objects["source_id"],
            AnomalyType.NO_EVENTS.value,
            starts_at=datetime.now(UTC) + timedelta(hours=1),
            ends_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add_all([narrow, wide, ended, future])
        await session.flush()
        session.add_all([
            AlertSuppressionHit(
                suppression_id=narrow.id,
                family="log_source",
                anomaly_id=uuid.uuid4(),
                detector_type=AnomalyType.VOLUME_SPIKE.value,
                bucket_start=NOW,
                occurred_at=NOW,
            ),
            AlertSuppressionHit(
                suppression_id=narrow.id,
                family="log_source",
                anomaly_id=uuid.uuid4(),
                detector_type=AnomalyType.VOLUME_SPIKE.value,
                bucket_start=NOW,
                occurred_at=NOW,
            ),
            AlertSuppressionHit(
                suppression_id=wide.id,
                family="log_source",
                anomaly_id=uuid.uuid4(),
                detector_type=AnomalyType.VOLUME_SPIKE.value,
                bucket_start=NOW,
                occurred_at=NOW,
            ),
        ])
        await session.flush()
        return str(narrow.id), str(wide.id)

    narrow_id, wide_id = run(fixture)
    client = Client(VIEWER)
    active = client.get(f"{BASE}/suppressions?active=true").json()
    assert len(active) == 3
    by_id = {row["id"]: row for row in active}
    assert by_id[narrow_id]["hit_count"] == 2
    assert by_id[wide_id]["hit_count"] == 1
    maintenance = next(row for row in active if row["kind"] == "MAINTENANCE")
    assert maintenance["read_only"] is True and maintenance["hit_count"] is None
    assert maintenance["subject_name"] == "edge-01"
    assert maintenance["reason"] == "maintenance note"
    historical = client.get(f"{BASE}/suppressions?active=false").json()
    assert len(historical) == 4
    assert all(row["kind"] != "MAINTENANCE" for row in historical)


def test_list_statement_count_is_constant_at_two_sizes(db_schema) -> None:
    objects = _setup_phase()
    client = Client(VIEWER)
    counts = []
    active_counts = []
    for total in (1, 6):
        async def add(session, total=total):  # type: ignore[no-untyped-def]
            existing = int(
                await session.scalar(select(func.count()).select_from(AlertSuppression)) or 0
            )
            session.add_all([
                _suppression_row(objects["source_id"], f"DETECTOR_{index}")
                for index in range(existing, total)
            ])
            await session.flush()

        run(add)
        assert len(client.get(f"{BASE}/suppressions?active=false").json()) == total
        counts.append(client.statements)
        assert len(client.get(f"{BASE}/suppressions?active=true").json()) == total
        active_counts.append(client.statements)
    # Suppressions + grouped hits; active=true adds the one maintenance read.
    assert counts == [2, 2]
    assert active_counts == [3, 3]


def test_condition_read_returns_hits_and_window_alerts_then_an_honest_null(
    db_schema,
) -> None:
    objects = _setup_phase()

    async def fixture(session):  # type: ignore[no-untyped-def]
        row = _suppression_row(objects["source_id"], AnomalyType.VOLUME_SPIKE.value)
        session.add(row)
        await session.flush()
        session.add(
            AlertSuppressionHit(
                suppression_id=row.id,
                family="log_source",
                anomaly_id=uuid.UUID(objects["anomaly_id"]),
                detector_type=AnomalyType.VOLUME_SPIKE.value,
                bucket_start=NOW,
                occurred_at=NOW,
            )
        )
        # The stored alert was opened at a fixed clock. Move it inside the
        # suppression window, which is anchored to the real clock, so the
        # assertion does not depend on what time the suite runs.
        stored = await session.get(Alert, uuid.UUID(objects["alert_id"]))
        assert stored is not None
        stored.opened_at = datetime.now(UTC)
        outside = _alert(
            source_id=uuid.UUID(objects["source_id"]),
            anomaly_id=uuid.UUID(objects["anomaly_id"]),
            opened_at=datetime.now(UTC) - timedelta(days=2),
        )
        session.add(outside)
        await session.flush()
        return str(row.id)

    suppression_id = run(fixture)
    client = Client(VIEWER)
    response = client.get(
        f"{BASE}/suppressions/for/log_source/{objects['anomaly_id']}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suppression"]["id"] == suppression_id
    assert body["hit_count"] == 1 and body["suppression"]["hit_count"] == 1
    assert [row["id"] for row in body["breakthrough_alerts"]] == [objects["alert_id"]]

    assert Client(ANALYST).post(f"{BASE}/suppressions/{suppression_id}/end").status_code == 200
    inactive = client.get(
        f"{BASE}/suppressions/for/log_source/{objects['anomaly_id']}"
    ).json()
    assert inactive == {
        "family": "log_source",
        "anomaly_id": objects["anomaly_id"],
        "detector_type": AnomalyType.VOLUME_SPIKE.value,
        "suppression": None,
        "hit_count": 0,
        "breakthrough_alerts": [],
    }


def test_a_rule_has_no_whole_source_scope(db_schema) -> None:
    """SOC-6f: whole_source on a rule anomaly or an analytics_rule alert is 422.

    The refusal is the rule sentence. Property watch keeps its own sentence.
    """

    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        rule = AnalyticsRule(
            instance_id=instance.id,
            qradar_id=61,
            name="scoped-rule",
            rule_type="EVENT",
            enabled=True,
        )
        session.add(rule)
        await session.flush()
        anomaly = RuleAnomaly(
            rule_id=rule.id,
            qradar_id=rule.qradar_id,
            detector_type="RULE_SILENCE",
            fingerprint="rule_anomaly:test",
            severity=Severity.MEDIUM.value,
            state=AnomalyState.OPEN.value,
            detected_at=NOW,
        )
        session.add(anomaly)
        await session.flush()
        alert = _alert(
            source_id=rule.id,
            anomaly_id=anomaly.id,
            source_type="analytics_rule",
            title="Rule silence: scoped-rule",
        )
        session.add(alert)
        await session.flush()
        return str(anomaly.id), str(alert.id)

    anomaly_id, alert_id = run(setup)
    client = Client(ANALYST)
    body = _body(whole_source=True)
    anomaly_response = client.post(
        f"{BASE}/anomalies/rule/{anomaly_id}/suppress", json=body
    )
    assert anomaly_response.status_code == 422, anomaly_response.text
    assert "a rule has no whole-source scope" in anomaly_response.text
    alert_response = client.post(f"{BASE}/alerts/{alert_id}/suppress", json=body)
    assert alert_response.status_code == 422, alert_response.text
    assert "a rule has no whole-source scope" in alert_response.text
    assert client.get(f"{BASE}/suppressions?active=false").json() == []


def test_an_identity_incident_suppresses_on_its_log_source(db_schema) -> None:
    """The condition is the log source, and the identity alert is the breakthrough.

    Whole-source on the alert stays on that log source. A property-watch lookup
    would 404, and the old whole-source fall-through would say the alert names
    no log source.
    """

    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        source = await make_log_source(session, instance, qradar_id=6201, name="dc-01")
        anomaly = IdentityAnomaly(
            log_source_id=source.id,
            event_id=4625,
            detector_type=IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE,
            fingerprint=f"fp-{uuid.uuid4().hex[:12]}",
            severity=Severity.HIGH,
            state=AnomalyState.OPEN,
            detected_at=NOW,
            consecutive_buckets=1,
            policy_version=1,
            profile_version=1,
            catalog_version=1,
            details={},
            evidence_status=EvidenceStatus.COMPLETE,
            observed_measurement=IdentityMeasurement.EXACT,
        )
        session.add(anomaly)
        await session.flush()
        alert = _alert(
            source_id=source.id,
            anomaly_id=anomaly.id,
            source_type="identity_source",
            title="Failed logon spike on dc-01 (4625)",
            opened_at=datetime.now(UTC) + timedelta(seconds=5),
        )
        session.add(alert)
        await session.flush()
        return str(source.id), str(anomaly.id), str(alert.id)

    source_id, anomaly_id, alert_id = run(setup)
    client = Client(ANALYST)
    created = client.post(
        f"{BASE}/anomalies/identity/{anomaly_id}/suppress", json=_body()
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["family"] == "identity"
    assert body["subject_id"] == source_id
    assert body["detector_type"] == IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE.value
    listed = client.get(f"{BASE}/suppressions/for/identity/{anomaly_id}")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["breakthrough_alerts"]] == [alert_id]
    wide = client.post(
        f"{BASE}/alerts/{alert_id}/suppress", json=_body(whole_source=True)
    )
    assert wide.status_code == 200, wide.text
    assert wide.json()["family"] == "identity"
    assert wide.json()["subject_id"] == source_id
    assert wide.json()["detector_type"] is None


def test_a_rule_alert_breaks_through_on_the_rule(db_schema) -> None:
    """A rule suppression reads analytics_rule alerts, not property-watch values."""

    async def setup(session):  # type: ignore[no-untyped-def]
        instance = await make_instance(session)
        rule = AnalyticsRule(
            instance_id=instance.id,
            qradar_id=6202,
            name="scoped-rule",
            rule_type="EVENT",
            enabled=True,
        )
        session.add(rule)
        await session.flush()
        anomaly = RuleAnomaly(
            rule_id=rule.id,
            qradar_id=rule.qradar_id,
            detector_type="RULE_SILENCE",
            fingerprint=f"rule_anomaly:{uuid.uuid4().hex[:12]}",
            severity=Severity.MEDIUM.value,
            state=AnomalyState.OPEN.value,
            detected_at=NOW,
        )
        session.add(anomaly)
        await session.flush()
        alert = _alert(
            source_id=rule.id,
            anomaly_id=anomaly.id,
            source_type="analytics_rule",
            title="Rule silence: scoped-rule",
            opened_at=datetime.now(UTC) + timedelta(seconds=5),
        )
        session.add(alert)
        await session.flush()
        return str(anomaly.id), str(alert.id)

    anomaly_id, alert_id = run(setup)
    client = Client(ANALYST)
    created = client.post(f"{BASE}/anomalies/rule/{anomaly_id}/suppress", json=_body())
    assert created.status_code == 200, created.text
    listed = client.get(f"{BASE}/suppressions/for/rule/{anomaly_id}")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["breakthrough_alerts"]] == [alert_id]
