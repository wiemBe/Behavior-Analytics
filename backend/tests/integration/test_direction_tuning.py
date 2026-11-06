"""The direction knob's engine skip and its recorded PATCH side effect (SOC-17b).

Design §3.1 and D-54: disabling a direction also closes the live incidents of
the excluded detector through an `AnomalyStateTransition`, because a skipped
detector can never resolve them itself. These tests drive the real engine for
the skip and the real route for the closure, the audit and the closure's
absence when the direction widens again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Severity,
)
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
)
from app.noise.backtest import NoiseBacktest, ReplayState
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import add_metric

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
BUCKET = 300

WRITER = Principal(
    subject="writer", permissions=frozenset({"read:*", "log_source:write"})
)


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_deviation_threshold=3.5)


def writer_client(db_url: str) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: WRITER
    client = TestClient(app)
    client.engine = engine  # type: ignore[attr-defined]
    return client


async def _source(session, *, qradar_id: int = 1, **kwargs) -> LogSource:
    instance = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(instance)
    await session.flush()
    source = LogSource(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=f"src-{qradar_id}",
        expected_interval_seconds=BUCKET,
        **kwargs,
    )
    session.add(source)
    await session.flush()
    return source


async def _cells(session, source: LogSource, *, median: float = 50.0) -> None:
    for metric, value, mad in (
        ("average_eps", median, 2.0),
        ("event_count", median * BUCKET, 500.0),
    ):
        session.add(
            LogSourceBaseline(
                log_source_id=source.id,
                metric_name=metric,
                weekday=START.isoweekday(),
                hour=START.hour,
                median=value,
                mad=mad,
                p05=value * 0.9,
                p95=value * 1.1,
                sample_count=30,
                is_reliable=True,
                completeness=0.95,
                baseline_version=3,
                observations=[value],
                computed_at=START,
            )
        )
    await session.flush()


async def _feed(session, source, *, count: int = 3, eps: float = 5.0):
    metrics = []
    for index in range(count):
        metrics.append(
            await add_metric(
                session,
                source,
                START + timedelta(seconds=BUCKET * index),
                average_eps=eps,
                event_count=int(eps * BUCKET),
                bucket_seconds=BUCKET,
                completeness=BucketCompleteness.COMPLETE,
                payload_signature=f"sig-{index}",
            )
        )
    return metrics


async def _evaluate(session, source, metrics) -> list[str]:
    engine = AnomalyEngine(session, settings=_settings())
    opened: list[str] = []
    for metric in metrics:
        report = await engine.evaluate_interval(source, metric)
        opened.extend(report.opened)
    return opened


async def _detector_types(session, source) -> set[str]:
    return set(
        (
            await session.scalars(
                select(LogSourceDetectorState.anomaly_type).where(
                    LogSourceDetectorState.log_source_id == source.id
                )
            )
        ).all()
    )


# ---------------------------------------------------------------- the engine skip
async def test_spike_only_runs_the_spike_detector_and_not_the_drop(db_session) -> None:
    source = await _source(db_session, custom_thresholds={"direction": "spike"})
    await _cells(db_session, source)
    metrics = await _feed(db_session, source, eps=5.0)
    await _evaluate(db_session, source, metrics)

    types = await _detector_types(db_session, source)
    assert "VOLUME_SPIKE" in types
    assert "VOLUME_DROP" not in types
    drop_incidents = await db_session.scalar(
        select(func.count())
        .select_from(LogSourceAnomaly)
        .where(
            LogSourceAnomaly.log_source_id == source.id,
            LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert drop_incidents == 0


async def test_drop_only_runs_the_drop_detector_and_not_the_spike(db_session) -> None:
    source = await _source(db_session, custom_thresholds={"direction": "drop"})
    await _cells(db_session, source)
    metrics = await _feed(db_session, source, eps=150.0)
    await _evaluate(db_session, source, metrics)

    types = await _detector_types(db_session, source)
    assert "VOLUME_DROP" in types
    assert "VOLUME_SPIKE" not in types
    spike_incidents = await db_session.scalar(
        select(func.count())
        .select_from(LogSourceAnomaly)
        .where(
            LogSourceAnomaly.log_source_id == source.id,
            LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_SPIKE,
        )
    )
    assert spike_incidents == 0


async def test_both_runs_every_detector(db_session) -> None:
    source = await _source(db_session, custom_thresholds={"direction": "both"})
    await _cells(db_session, source)
    metrics = await _feed(db_session, source, eps=5.0)
    await _evaluate(db_session, source, metrics)

    types = await _detector_types(db_session, source)
    assert {"VOLUME_SPIKE", "VOLUME_DROP"} <= types


async def test_by_type_detector_thresholds_stay_inert_in_the_engine(db_session) -> None:
    source = await _source(
        db_session,
        custom_thresholds={"by_type": {"VOLUME_SPIKE": {"deviation_z": 1000}}},
    )
    await _cells(db_session, source)
    metrics = await _feed(db_session, source, eps=150.0)
    opened = await _evaluate(db_session, source, metrics)
    assert "VOLUME_SPIKE" in opened


# ------------------------------------------------------------- the PATCH side effect
async def _open_incident(
    session, source: LogSource, kind: AnomalyType, *, with_alert: bool = False
) -> tuple[LogSourceAnomaly, Alert | None]:
    incident = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=kind,
        severity=Severity.MEDIUM,
        state=AnomalyState.OPEN,
        detected_at=START,
        opened_at=START,
        observed_value=5.0,
        expected_value=50.0,
        resolved_at=None,
    )
    session.add(incident)
    await session.flush()
    session.add(
        LogSourceDetectorState(
            log_source_id=source.id,
            anomaly_type=kind,
            state=AnomalyState.OPEN,
            is_open=True,
            active_anomaly_id=incident.id,
            open_anomaly_id=incident.id,
            consecutive_anomalous=2,
            consecutive_healthy=0,
            last_interval_start=START,
        )
    )
    alert = None
    if with_alert:
        fingerprint = anomaly_fingerprint(source.id, kind.value)
        alert = Alert(
            dedup_key=fingerprint,
            fingerprint=fingerprint,
            title=f"{kind.value} on {source.name}",
            severity=Severity.MEDIUM,
            status=AlertStatus.OPEN,
            source_type="log_source",
            source_id=source.id,
            source_anomaly_ids=[str(incident.id)],
            opened_at=START,
            first_seen_at=START,
        )
        session.add(alert)
    await session.flush()
    return incident, alert


async def _seed_for_patch(
    session,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    source = await _source(session)
    drop, drop_alert = await _open_incident(
        session, source, AnomalyType.VOLUME_DROP, with_alert=True
    )
    spike, _ = await _open_incident(session, source, AnomalyType.VOLUME_SPIKE)
    ids = (source.id, drop.id, drop_alert.id, spike.id)
    await session.commit()
    return ids


async def test_disabling_a_direction_closes_its_incident_and_alert(db_session, db_schema) -> None:
    source_id, drop_id, drop_alert_id, spike_id = await _seed_for_patch(db_session)
    client = writer_client(db_schema)
    response = client.patch(
        f"/api/v1/log-sources/{source_id}",
        json={"custom_thresholds": {"direction": "spike"}},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    resolved = await db_session.get(LogSourceAnomaly, drop_id)
    untouched = await db_session.get(LogSourceAnomaly, spike_id)
    alert = await db_session.get(Alert, drop_alert_id)
    assert resolved is not None and AnomalyState(resolved.state) is AnomalyState.RESOLVED
    assert resolved.resolved_at is not None
    assert untouched is not None and AnomalyState(untouched.state) is AnomalyState.OPEN
    assert alert is not None and alert.status == AlertStatus.RESOLVED
    assert alert.resolution_reason_i18n is not None
    assert alert.resolution_reason_i18n["code"] == "anomaly.tuning.direction_disabled"

    transitions = list(
        (
            await db_session.scalars(
                select(AnomalyStateTransition).where(
                    AnomalyStateTransition.anomaly_id == drop_id
                )
            )
        ).all()
    )
    assert len(transitions) == 1
    transition = transitions[0]
    assert AnomalyState(transition.from_state) is AnomalyState.OPEN
    assert AnomalyState(transition.to_state) is AnomalyState.RESOLVED
    assert transition.actor == "writer"
    assert transition.reason_i18n is not None
    assert transition.reason_i18n["code"] == "anomaly.tuning.direction_disabled"

    state = await db_session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == source_id,
            LogSourceDetectorState.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert state is not None
    assert AnomalyState(state.state) is AnomalyState.NORMAL
    assert state.is_open is False
    assert state.active_anomaly_id is None and state.open_anomaly_id is None

    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "log_source.update").order_by(
            AuditLog.created_at.desc()
        )
    )
    assert audit is not None
    assert audit.detail["changes"]["custom_thresholds"]["to"] == {"direction": "spike"}
    closed = audit.detail["closed_incidents"]
    assert [item["anomaly_type"] for item in closed] == ["VOLUME_DROP"]
    assert closed[0]["incidents"] == 1
    assert closed[0]["alert_resolved"] is True


async def test_switching_back_to_both_closes_nothing(db_session, db_schema) -> None:
    source_id, drop_id, _, spike_id = await _seed_for_patch(db_session)
    client = writer_client(db_schema)
    assert client.patch(
        f"/api/v1/log-sources/{source_id}",
        json={"custom_thresholds": {"direction": "spike"}},
    ).status_code == 200
    db_session.expire_all()
    before = await db_session.scalar(
        select(func.count())
        .select_from(AnomalyStateTransition)
        .where(AnomalyStateTransition.anomaly_id == drop_id)
    )

    assert client.patch(
        f"/api/v1/log-sources/{source_id}",
        json={"custom_thresholds": {"direction": "both"}},
    ).status_code == 200
    db_session.expire_all()
    after = await db_session.scalar(
        select(func.count())
        .select_from(AnomalyStateTransition)
        .where(AnomalyStateTransition.anomaly_id == drop_id)
    )
    assert after == before == 1
    spike_row = await db_session.get(LogSourceAnomaly, spike_id)
    assert spike_row is not None
    assert AnomalyState(spike_row.state) is AnomalyState.OPEN


# --------------------------------------------------------------- audit and validation
async def test_the_patch_records_the_preview_block_without_applying_it(
    db_session, db_schema
) -> None:
    source = await _source(db_session)
    source_id = source.id
    await db_session.commit()
    client = writer_client(db_schema)
    preview = {
        "at": "2026-11-06T12:00:00Z",
        "window": 28,
        "current": 39,
        "proposed": 7,
        "state": "COMPLETE",
    }
    response = client.patch(
        f"/api/v1/log-sources/{source_id}",
        json={"owner": "ada", "preview": preview},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    row = await db_session.get(LogSource, source_id)
    assert row is not None and row.owner == "ada"
    assert row.custom_thresholds == {}
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "log_source.update").order_by(
            AuditLog.created_at.desc()
        )
    )
    assert audit is not None
    assert audit.detail["preview"]["at"] == "2026-11-06T12:00:00Z"
    assert audit.detail["preview"]["current"] == 39
    assert audit.detail["preview"]["proposed"] == 7
    assert "closed_incidents" not in audit.detail


async def test_a_malformed_preview_block_is_refused(db_session, db_schema) -> None:
    source = await _source(db_session)
    await db_session.commit()
    client = writer_client(db_schema)
    response = client.patch(
        f"/api/v1/log-sources/{source.id}",
        json={"preview": {"at": "2026-11-06T12:00:00Z", "window": 28, "state": "NORMAL"}},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"custom_thresholds": {"not_a_knob": 1}},
        {"custom_thresholds": {"spike_ratio": 0.4}},
        {"custom_thresholds": {"deviation_z": "3.5"}},
        {"custom_thresholds": {"direction": "up"}},
        {"custom_thresholds": {"by_type": {"VOLUME_SPIKE": {"deviation_z": 21}}}},
    ],
)
async def test_invalid_thresholds_are_422(db_session, db_schema, body: dict) -> None:
    source = await _source(db_session)
    await db_session.commit()
    client = writer_client(db_schema)
    response = client.patch(f"/api/v1/log-sources/{source.id}", json=body)
    assert response.status_code == 422


async def test_legacy_threshold_keys_are_still_accepted(db_session, db_schema) -> None:
    source = await _source(db_session)
    source_id = source.id
    await db_session.commit()
    client = writer_client(db_schema)
    legacy = {
        "min_bucket_events": 10,
        "resolve_after": 5,
        "silence_grace_buckets": 0,
        "by_type": {"NO_EVENTS": {"resolve_after": 4}},
    }
    response = client.patch(
        f"/api/v1/log-sources/{source_id}", json={"custom_thresholds": legacy}
    )
    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = await db_session.get(LogSource, source_id)
    assert row is not None and row.custom_thresholds == legacy


async def test_a_stored_row_with_an_unknown_key_is_still_readable(
    db_session, db_schema
) -> None:
    """Reads never reject a stored row; only a new write is validated."""
    source = await _source(db_session, custom_thresholds={"stored_typo": 1})
    await db_session.commit()

    client = writer_client(db_schema)
    detail = client.get(f"/api/v1/log-sources/{source.id}")
    assert detail.status_code == 200
    assert detail.json()["custom_thresholds"] == {"stored_typo": 1}
    # A PATCH that does not carry the field does not re-validate the stored one.
    assert client.patch(
        f"/api/v1/log-sources/{source.id}", json={"owner": "ada"}
    ).status_code == 200

    result = await NoiseBacktest(
        db_session, settings=_settings(), now=START + timedelta(days=7)
    ).preview(source.id, window_days=7)
    assert result is not None
    assert result.state is ReplayState.INSUFFICIENT_DATA
