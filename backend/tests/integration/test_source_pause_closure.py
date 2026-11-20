"""Bulk closure of the volume incidents of sources QRadar reports disabled (JDBC-1 P3b).

A disabled source stops sending, so its detectors can never resolve what they opened. The
service closes those incidents for many sources at once, says it reached no verdict, and
makes the sources' baseline unusable so a later re-enable cannot judge against a band the
disabled tail contaminated. Nothing calls it yet; these tests call it the way the inventory
edge will, and pin the recorded facts, the per-call statement count and the repeat call.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, inspect, select

from app.alerts.fingerprint import anomaly_fingerprint
from app.i18n.messages import MessageRef, render
from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    AlertStatus,
    AlertTransition,
    AnomalyState,
    AnomalyType,
    NotificationChannel,
    Severity,
)
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
)
from app.services import source_incident_closure
from app.services.source_incident_closure import close_for_disabled_sources
from tests.integration import test_source_closure_extraction as p3a
from tests.integration.test_direction_tuning import START, _cells, _source

pytestmark = pytest.mark.integration

OBSERVED_AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SENTENCE = "Closed without a verdict: source disabled in QRadar."
ACTOR = "system:inventory-sync"
HOOK = "https://hooks.example.test/pause"
END_BEFORE = START + timedelta(minutes=20)


async def _incident(
    session,
    source: LogSource,
    kind: AnomalyType,
    state: AnomalyState,
    *,
    suppressed: bool = False,
    alert: AlertStatus | None = None,
    anomaly_end: datetime | None = None,
    resolved_at: datetime | None = None,
    detector: bool = True,
) -> tuple[LogSourceAnomaly, Alert | None]:
    incident = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=kind,
        severity=Severity.MEDIUM,
        state=state,
        detected_at=START,
        opened_at=START,
        anomaly_start=START,
        anomaly_end=anomaly_end,
        resolved_at=resolved_at,
        observed_value=5.0,
        expected_value=50.0,
        suppressed=suppressed,
    )
    session.add(incident)
    await session.flush()
    if detector:
        session.add(
            LogSourceDetectorState(
                log_source_id=source.id,
                anomaly_type=kind,
                state=state if state is not AnomalyState.RESOLVED else AnomalyState.NORMAL,
                is_open=state is AnomalyState.OPEN,
                active_anomaly_id=incident.id,
                open_anomaly_id=incident.id if state is AnomalyState.OPEN else None,
                consecutive_anomalous=2,
                consecutive_healthy=1,
                last_interval_start=START,
            )
        )
    row = None
    if alert is not None:
        fingerprint = anomaly_fingerprint(source.id, kind.value)
        row = Alert(
            dedup_key=fingerprint,
            fingerprint=fingerprint,
            title=f"{kind.value} on {source.name}",
            severity=Severity.MEDIUM,
            status=alert,
            source_type="log_source",
            source_id=source.id,
            source_anomaly_ids=[str(incident.id)],
            opened_at=START,
            first_seen_at=START,
        )
        session.add(row)
    await session.flush()
    return incident, row


async def _fresh(session, model, key):
    session.expire_all()
    row = await session.get(model, key)
    assert row is not None
    return row


async def _transitions(session, anomaly_id) -> list[AnomalyStateTransition]:
    return list(
        (
            await session.scalars(
                select(AnomalyStateTransition).where(
                    AnomalyStateTransition.anomaly_id == anomaly_id
                )
            )
        ).all()
    )


async def _detector(session, source_id, kind) -> LogSourceDetectorState:
    row = await session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == source_id,
            LogSourceDetectorState.anomaly_type == kind,
        )
    )
    assert row is not None
    return row


@contextmanager
def _counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_the_two_messages_say_one_sentence_and_no_health() -> None:
    for code in (
        "anomaly.source_disabled_without_verdict",
        "alert.source_disabled_without_verdict",
    ):
        text = render(MessageRef(code))
        assert text == SENTENCE
        assert "recover" not in text.lower() and "healthy" not in text.lower()


async def test_candidate_open_recovering_suppressed_close_without_verdict(db_session) -> None:
    source = await _source(db_session, qradar_id=1)
    other = await _source(db_session, qradar_id=2)
    candidate, _ = await _incident(
        db_session, source, AnomalyType.NO_EVENTS, AnomalyState.CANDIDATE
    )
    opened, _ = await _incident(db_session, source, AnomalyType.VOLUME_DROP, AnomalyState.OPEN)
    recovering, _ = await _incident(
        db_session,
        source,
        AnomalyType.VOLUME_SPIKE,
        AnomalyState.RECOVERING,
        anomaly_end=END_BEFORE,
    )
    suppressed, _ = await _incident(
        db_session, other, AnomalyType.VOLUME_DROP, AnomalyState.OPEN, suppressed=True
    )
    bystander, _ = await _incident(
        db_session, await _source(db_session, qradar_id=3), AnomalyType.VOLUME_DROP,
        AnomalyState.OPEN,
    )
    ids = {
        "candidate": candidate.id, "open": opened.id, "recovering": recovering.id,
        "suppressed": suppressed.id, "bystander": bystander.id,
    }
    source_id, other_id = source.id, other.id
    await db_session.commit()

    summary = await close_for_disabled_sources(
        db_session, [source_id, other_id, source_id], observed_at=OBSERVED_AT
    )
    assert (summary.sources, summary.incidents_closed) == (2, 4)

    for name in ("candidate", "open", "recovering", "suppressed"):
        row = await _fresh(db_session, LogSourceAnomaly, ids[name])
        assert AnomalyState(row.state) is AnomalyState.RESOLVED, name
        assert row.resolved_at == OBSERVED_AT, name
    # An open end is bounded at the observation; a set one is never moved.
    assert (await _fresh(db_session, LogSourceAnomaly, ids["open"])).anomaly_end == OBSERVED_AT
    assert (await _fresh(db_session, LogSourceAnomaly, ids["candidate"])).anomaly_end == OBSERVED_AT
    assert (
        await _fresh(db_session, LogSourceAnomaly, ids["recovering"])
    ).anomaly_end == END_BEFORE
    untouched = await _fresh(db_session, LogSourceAnomaly, ids["bystander"])
    assert AnomalyState(untouched.state) is AnomalyState.OPEN and untouched.resolved_at is None
    assert await _transitions(db_session, ids["bystander"]) == []

    for kind in (AnomalyType.NO_EVENTS, AnomalyType.VOLUME_DROP, AnomalyType.VOLUME_SPIKE):
        state = await _detector(db_session, source_id, kind)
        assert AnomalyState(state.state) is AnomalyState.INSUFFICIENT_DATA
        assert state.is_open is False
        assert state.open_anomaly_id is None and state.active_anomaly_id is None
        assert state.consecutive_anomalous == 0 and state.consecutive_healthy == 0
    kept = await _detector(db_session, await _source_id_of(db_session, ids["bystander"]),
                           AnomalyType.VOLUME_DROP)
    assert AnomalyState(kept.state) is AnomalyState.OPEN and kept.consecutive_anomalous == 2


async def _source_id_of(session, anomaly_id) -> uuid.UUID:
    return (await _fresh(session, LogSourceAnomaly, anomaly_id)).log_source_id


async def test_open_ack_alerts_reason_and_notification(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_GENERIC_WEBHOOK_URL", HOOK)
    source = await _source(db_session, qradar_id=1)
    _, open_alert = await _incident(
        db_session, source, AnomalyType.VOLUME_DROP, AnomalyState.OPEN, alert=AlertStatus.OPEN
    )
    _, ack_alert = await _incident(
        db_session, source, AnomalyType.VOLUME_SPIKE, AnomalyState.OPEN,
        alert=AlertStatus.ACKNOWLEDGED,
    )
    assert open_alert is not None and ack_alert is not None
    open_id, ack_id, source_id = open_alert.id, ack_alert.id, source.id
    await db_session.commit()

    summary = await close_for_disabled_sources(db_session, [source_id], observed_at=OBSERVED_AT)
    assert (summary.alerts_resolved, summary.notifications_enqueued) == (2, 2)

    for alert_id in (open_id, ack_id):
        alert = await _fresh(db_session, Alert, alert_id)
        assert alert.status == AlertStatus.RESOLVED
        assert alert.resolved_at == OBSERVED_AT and alert.resolved_by == ACTOR
        assert alert.resolution_reason == SENTENCE
        assert alert.resolution_reason_i18n == MessageRef(
            "alert.source_disabled_without_verdict"
        ).to_wire()
        (notification,) = (
            await db_session.scalars(
                select(AlertNotification).where(AlertNotification.alert_id == alert_id)
            )
        ).all()
        assert notification.transition == AlertTransition.RESOLVED
        assert notification.channel == NotificationChannel.WEBHOOK
        assert notification.target == HOOK
        # The payload carries the code of the reason, which renders as the exact sentence.
        code = notification.payload["resolution_reason_code"]
        assert code == "alert.source_disabled_without_verdict"
        assert render(MessageRef(code)) == SENTENCE


async def test_no_driving_bucket_or_values(db_session) -> None:
    source = await _source(db_session, qradar_id=1)
    incident, _ = await _incident(
        db_session, source, AnomalyType.VOLUME_DROP, AnomalyState.RECOVERING
    )
    incident_id, source_id = incident.id, source.id
    await db_session.commit()

    await close_for_disabled_sources(
        db_session, [source_id], observed_at=OBSERVED_AT, actor="system:test"
    )

    db_session.expire_all()
    (transition,) = await _transitions(db_session, incident_id)
    assert AnomalyState(transition.from_state) is AnomalyState.RECOVERING
    assert AnomalyState(transition.to_state) is AnomalyState.RESOLVED
    assert transition.occurred_at == OBSERVED_AT
    assert transition.bucket_start is None
    assert transition.observed_value is None and transition.expected_value is None
    assert transition.actor == "system:test"
    assert transition.reason == SENTENCE
    assert transition.reason_i18n == MessageRef(
        "anomaly.source_disabled_without_verdict"
    ).to_wire()
    # The incident keeps its own figures; only the transition gives no values.
    incident = await _fresh(db_session, LogSourceAnomaly, incident_id)
    assert (incident.observed_value, incident.expected_value) == (5.0, 50.0)


async def _seed_fleet(session, count: int, *, first: int) -> list[uuid.UUID]:
    """Sources with an open incident and alert, a detector row and reliable cells."""
    ids = []
    for index in range(count):
        source = await _source(session, qradar_id=first + index)
        await _incident(
            session, source, AnomalyType.VOLUME_DROP, AnomalyState.OPEN, alert=AlertStatus.OPEN
        )
        await _incident(session, source, AnomalyType.NO_EVENTS, AnomalyState.CANDIDATE)
        await _cells(session, source)
        ids.append(source.id)
    await session.commit()
    return ids


def _shape(statements: list[str]) -> list[str]:
    return [" ".join(text.split())[:60] for text in statements]


async def test_idempotence_bulk_lookup_and_baseline_invalidation(db_session) -> None:
    (lone,) = await _seed_fleet(db_session, 1, first=1)
    with _counting(db_session) as one:
        await close_for_disabled_sources(db_session, [lone], observed_at=OBSERVED_AT)
    fleet = await _seed_fleet(db_session, 50, first=10)
    with _counting(db_session) as fifty:
        summary = await close_for_disabled_sources(db_session, fleet, observed_at=OBSERVED_AT)

    # One chunk either way: the same statements, however many sources.
    assert len(fifty) == len(one) > 0
    assert _shape(fifty) == _shape(one)
    lookups = [s for s in fifty if "FROM alert" in s and "dedup_key" in s]
    assert len(lookups) == 1
    assert (summary.sources, summary.incidents_closed, summary.alerts_resolved) == (50, 100, 50)
    assert summary.baseline_cells_invalidated == 100  # two reliable cells per source

    cells = list(
        (
            await db_session.scalars(
                select(LogSourceBaseline).where(LogSourceBaseline.log_source_id.in_(fleet))
            )
        ).all()
    )
    assert len(cells) == 100
    assert all(not c.is_reliable and c.sample_count == 0 and c.p05 is None for c in cells)
    assert all(c.baseline_version == 4 for c in cells)  # seeded at 3, moved once

    async def _counts() -> tuple[int, int, int, int]:
        return tuple(  # type: ignore[return-value]
            [
                await db_session.scalar(select(func.count()).select_from(model))
                for model in (AnomalyStateTransition, AlertNotification, Alert)
            ]
            + [
                await db_session.scalar(
                    select(func.sum(LogSourceBaseline.baseline_version)).where(
                        LogSourceBaseline.log_source_id.in_(fleet)
                    )
                )
            ]
        )

    before = await _counts()
    with _counting(db_session) as again:
        repeat = await close_for_disabled_sources(
            db_session, fleet, observed_at=OBSERVED_AT + timedelta(hours=1)
        )
    assert (repeat.incidents_closed, repeat.alerts_resolved) == (0, 0)
    assert (repeat.notifications_enqueued, repeat.baseline_cells_invalidated) == (0, 0)
    assert await _counts() == before
    # The repeat call finds no incident, so no alert lookup and no insert are sent.
    assert not [s for s in again if "FROM alert" in s]
    assert not [s for s in again if s.lstrip().upper().startswith("INSERT")]
    db_session.expire_all()
    resolved = await db_session.scalar(
        select(func.count()).select_from(LogSourceAnomaly).where(
            LogSourceAnomaly.resolved_at == OBSERVED_AT
        )
    )
    assert resolved >= 100  # the repeat call did not move resolved_at


async def test_chunks_add_statements_not_sources(db_session, monkeypatch) -> None:
    fleet = await _seed_fleet(db_session, 5, first=1)
    monkeypatch.setattr(source_incident_closure, "CHUNK", 2)
    with _counting(db_session) as statements:
        summary = await close_for_disabled_sources(db_session, fleet, observed_at=OBSERVED_AT)
    assert (summary.sources, summary.incidents_closed) == (5, 10)
    # Three chunks (2 + 2 + 1), one alert lookup each.
    assert len([s for s in statements if "FROM alert" in s and "dedup_key" in s]) == 3
    assert await close_for_disabled_sources(db_session, [], observed_at=OBSERVED_AT) == (
        source_incident_closure.DisabledClosureSummary()
    )


async def test_closed_and_other_types_untouched(db_session) -> None:
    source = await _source(db_session, qradar_id=1)
    closed_at = START + timedelta(hours=1)
    closed, closed_alert = await _incident(
        db_session, source, AnomalyType.VOLUME_DROP, AnomalyState.RESOLVED,
        anomaly_end=END_BEFORE, resolved_at=closed_at, detector=False,
    )
    other_type, other_alert = await _incident(
        db_session, source, AnomalyType.PARSING_DEGRADATION, AnomalyState.OPEN,
        alert=AlertStatus.OPEN,
    )
    # A resolved alert whose reason must stay as it was.
    assert closed_alert is None and other_alert is not None
    old = Alert(
        dedup_key="old-resolved", fingerprint="old-resolved", title="old", severity=Severity.LOW,
        status=AlertStatus.RESOLVED, source_type="log_source", source_id=source.id,
        opened_at=START, first_seen_at=START, resolved_at=closed_at, resolved_by="engine",
        resolution_reason="condition recovered",
    )
    db_session.add(old)
    await db_session.flush()
    ids = (closed.id, other_type.id, other_alert.id, old.id, source.id)
    closed_id, other_id, other_alert_id, old_id, source_id = ids
    await db_session.commit()

    summary = await close_for_disabled_sources(db_session, [source_id], observed_at=OBSERVED_AT)
    assert summary.incidents_closed == 0 and summary.alerts_resolved == 0

    row = await _fresh(db_session, LogSourceAnomaly, closed_id)
    assert row.resolved_at == closed_at and row.anomaly_end == END_BEFORE
    assert await _transitions(db_session, closed_id) == []
    row = await _fresh(db_session, LogSourceAnomaly, other_id)
    assert AnomalyState(row.state) is AnomalyState.OPEN and row.resolved_at is None
    assert (await _fresh(db_session, Alert, other_alert_id)).status == AlertStatus.OPEN
    alert = await _fresh(db_session, Alert, old_id)
    assert alert.resolved_by == "engine" and alert.resolution_reason == "condition recovered"
    state = await _detector(db_session, source_id, AnomalyType.PARSING_DEGRADATION)
    assert AnomalyState(state.state) is AnomalyState.OPEN and state.is_open is True


async def test_tuning_route_unchanged(db_session, db_schema, monkeypatch) -> None:
    """The direction-tuning PATCH still closes through P3a's entry with the tuning policy."""
    await p3a.test_direction_tuning_command_unchanged(db_session, db_schema, monkeypatch)


async def test_loaded_baseline_cells_are_not_served_stale(db_session) -> None:
    """A cell object the caller already holds shows the invalidation, not its old band."""
    (source_id,) = await _seed_fleet(db_session, 1, first=1)
    held = (
        await db_session.scalars(
            select(LogSourceBaseline).where(LogSourceBaseline.log_source_id == source_id)
        )
    ).first()
    assert held is not None and held.is_reliable is True

    await close_for_disabled_sources(db_session, [source_id], observed_at=OBSERVED_AT)

    # No expire_all here: the service itself must expire what the identity map holds, so the
    # next read loads the new row instead of serving the old band.
    assert {"is_reliable", "sample_count"} <= set(inspect(held).unloaded)
    await db_session.refresh(held)
    assert held.is_reliable is False and held.sample_count == 0
