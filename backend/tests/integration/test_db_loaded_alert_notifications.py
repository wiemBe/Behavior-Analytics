"""Notifications for lifecycle transitions on database-loaded alerts.

String-backed enum columns return plain strings after an ORM reload. These
regressions deliberately clear the identity map between opening an alert and
the transition that enqueues its notification.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import anomaly_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertInput, AlertService
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    AlertTransition,
    AnomalyType,
    Criticality,
    Severity,
)
from app.models.log_source import LogSource, LogSourceBaseline
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import add_metric, make_instance, make_log_source, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_deviation_threshold=3.5)


def _client(session) -> httpx.AsyncClient:
    async def _session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="analyst", permissions=frozenset({"admin:*"})
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _open_alert(session) -> uuid.UUID:
    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=f"anomaly:{uuid.uuid4().hex[:8]}",
            title="volume drop",
            severity=Severity.HIGH,
            source_type="log_source",
            source_id=uuid.uuid4(),
            evidence={"observed_value": 3.0},
        )
    )
    await session.commit()
    alert_id = result.alert.id
    session.expunge_all()
    return alert_id


async def _assert_plain_string_severity(session, alert_id: uuid.UUID) -> None:
    alert = await session.get(Alert, alert_id)
    assert alert is not None
    assert type(alert.severity) is str
    session.expunge(alert)


async def _notifications(session, alert_id: uuid.UUID) -> list[AlertNotification]:
    rows = await session.scalars(
        select(AlertNotification).where(AlertNotification.alert_id == alert_id)
    )
    return list(rows.all())


async def test_phase_a_recovery_enqueues_for_db_loaded_alert(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    instance = await make_instance(db_session)
    source = await make_log_source(
        db_session, instance, criticality=Criticality.MEDIUM
    )
    start = utc(h=10)
    db_session.add(
        LogSourceBaseline(
            log_source_id=source.id,
            metric_name="average_eps",
            weekday=start.isoweekday(),
            hour=start.hour,
            median=50.0,
            mad=2.0,
            p05=45.0,
            p95=55.0,
            sample_count=30,
            is_reliable=True,
            baseline_version=1,
            observations=[50.0],
        )
    )
    await db_session.flush()

    opening_engine = AnomalyEngine(db_session, settings=_settings())
    for index in range(2):
        metric = await add_metric(
            db_session,
            source,
            start + timedelta(minutes=5 * index),
            average_eps=3.0,
            event_count=900,
            payload_signature=f"open-{index}",
        )
        await opening_engine.evaluate_interval(source, metric)

    alert = await db_session.scalar(
        select(Alert).where(
            Alert.fingerprint
            == anomaly_fingerprint(source.id, AnomalyType.VOLUME_DROP.value)
        )
    )
    assert alert is not None
    alert_id = alert.id
    source_id = source.id
    await db_session.commit()
    db_session.expunge_all()
    await _assert_plain_string_severity(db_session, alert_id)

    source = await db_session.get(LogSource, source_id)
    assert source is not None
    dispatcher = NotificationDispatcher(
        db_session, notifiers={}, policy=default_policy(), settings=_settings()
    )
    recovery_engine = AnomalyEngine(
        db_session, settings=_settings(), enqueuer=dispatcher
    )
    for index in range(3):
        metric = await add_metric(
            db_session,
            source,
            start + timedelta(minutes=10 + 5 * index),
            average_eps=50.0,
            event_count=15000,
            payload_signature=f"recover-{index}",
        )
        await recovery_engine.evaluate_interval(source, metric)

    rows = await _notifications(db_session, alert_id)
    assert [row.transition for row in rows] == [AlertTransition.RESOLVED]


@pytest.mark.parametrize(
    ("path", "payload", "transition"),
    [
        ("acknowledge", None, AlertTransition.ACKNOWLEDGED),
        ("resolve", {"reason": "log source restored"}, AlertTransition.RESOLVED),
    ],
)
async def test_operator_transition_enqueues_for_db_loaded_alert(
    db_session, monkeypatch, path, payload, transition
) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", "https://hook.internal/alerts")
    alert_id = await _open_alert(db_session)
    await _assert_plain_string_severity(db_session, alert_id)

    async with _client(db_session) as client:
        response = await client.post(f"/api/v1/alerts/{alert_id}/{path}", json=payload)

    assert response.status_code == 200, response.text
    rows = await _notifications(db_session, alert_id)
    assert [row.transition for row in rows] == [transition]
