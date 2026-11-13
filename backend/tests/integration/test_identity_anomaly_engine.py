"""Identity engine: lifecycle, alert gates, and a statement count that does not grow."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.core.config import get_settings
from app.identity.engine import IdentityAnomalyEngine, fingerprint
from app.identity.fold import IdentitySeries, StoredUsername
from app.models.alert import Alert
from app.models.alert_policy import AlertPolicy
from app.models.alert_suppression import AlertSuppression, AlertSuppressionHit
from app.models.enums import (
    AlertPolicyMode,
    AlertPolicyScope,
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    IdentityAnomalyType,
    IdentityMeasurement,
    IdentityProfileValidationStatus,
    SuppressionKind,
)
from app.models.identity_anomaly import (
    IdentityAnomaly,
    IdentityAnomalyTransition,
    IdentityDetectorState,
)
from app.models.identity_detection import (
    IdentityBucket,
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
WIDTH = 300
EXACT = IdentityMeasurement.EXACT
TRUNCATED = IdentityMeasurement.TRUNCATED
MISSING = IdentityMeasurement.NOT_COLLECTED
DELETED = IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED.value
SPIKE = IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE.value
NOVEL = IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT.value


def _settings(**extra: object):
    return get_settings().model_copy(
        update={
            "identity_alerting_enabled": True,
            "identity_open_after_intervals": 1,
            "identity_resolve_after_intervals": 1,
            **extra,
        }
    )


def _series(
    source_id: uuid.UUID,
    event_id: int,
    start: datetime,
    count: int | None,
    *,
    measurement: IdentityMeasurement = EXACT,
    names: tuple[StoredUsername, ...] = (),
    names_measurement: IdentityMeasurement = EXACT,
    admission_truncated: bool = False,
    catalog: int = 1,
) -> IdentitySeries:
    return IdentitySeries(
        log_source_id=source_id,
        event_id=event_id,
        bucket_start=start,
        catalog_version=catalog,
        count=count,
        measurement=measurement,
        names_measurement=names_measurement,
        admission_truncated=admission_truncated,
        usernames=names,
    )


def _name(rank: int, username: str, count: int = 2) -> StoredUsername:
    return StoredUsername(rank=rank, username=username, count=count, truncated=False)


@contextmanager
def _counting(session: object) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _evaluate(session, instance_id, series, settings=None):
    engine = IdentityAnomalyEngine(session, settings=settings or _settings(), clock=lambda: NOW)
    return await engine.evaluate(instance_id=instance_id, series=series, bucket_seconds=WIDTH)


async def test_open_refresh_resolve_keeps_one_incident_and_one_alert(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc01")
    instance_id = instance.id
    source_id = source.id
    db_session.add(
        IdentitySourceProfile(
            instance_id=instance_id,
            log_source_id=source_id,
            enabled=True,
            profile_version=4,
            event_id_property="EventID",
            username_property="username",
            enabled_event_ids=[4726],
            validation_status=IdentityProfileValidationStatus.VALID,
            validation_report={},
        )
    )
    await db_session.commit()
    alice = (_name(1, "alice", 3),)
    first = await _evaluate(
        db_session,
        instance_id,
        [_series(source_id, 4726, NOW, 2, names=alice)],
    )
    await db_session.commit()
    incident = await db_session.scalar(select(IdentityAnomaly))
    alert = await db_session.scalar(select(Alert))
    assert first.incidents_opened == 1
    assert incident is not None and alert is not None
    assert incident.state == AnomalyState.OPEN.value
    assert incident.profile_version == 4
    assert incident.catalog_version == 1
    assert incident.details["usernames"] == [{"rank": 1, "username": "alice", "count": 3}]
    assert "alice" not in (incident.explanation or "")
    assert alert.source_type == "identity_source"
    assert alert.occurrence_count == 1
    assert alert.context["event_id"] == 4726
    assert alert.context["usernames"] == incident.details["usernames"]
    assert alert.title == "Account deleted on dc01 (4726)"
    assert alert.fingerprint == fingerprint(instance_id, source_id, 4726, DELETED)
    opened_id = incident.id

    second = await _evaluate(
        db_session,
        instance_id,
        [_series(source_id, 4726, NOW + timedelta(seconds=WIDTH), 3, names=alice)],
    )
    await db_session.commit()
    db_session.expire_all()
    incident = await db_session.scalar(select(IdentityAnomaly))
    alert = await db_session.scalar(select(Alert))
    transitions = int(
        await db_session.scalar(select(func.count()).select_from(IdentityAnomalyTransition)) or 0
    )
    assert second.incidents_refreshed == 1
    assert second.incidents_opened == 0
    assert incident is not None and incident.id == opened_id
    assert incident.state == AnomalyState.OPEN.value
    assert alert is not None and alert.occurrence_count == 2
    assert transitions == 1

    third = await _evaluate(
        db_session,
        instance_id,
        [_series(source_id, 4726, NOW + timedelta(seconds=WIDTH * 2), 0)],
    )
    await db_session.commit()
    db_session.expire_all()
    incident = await db_session.scalar(select(IdentityAnomaly))
    alert = await db_session.scalar(select(Alert))
    state = await db_session.scalar(select(IdentityDetectorState))
    assert third.incidents_resolved == 1
    assert incident is not None and incident.state == AnomalyState.RESOLVED.value
    assert alert is not None and alert.status == AlertStatus.RESOLVED.value
    assert state is not None
    assert state.active_anomaly_id is None
    assert state.open_anomaly_id is None


async def test_shadow_mode_records_the_incident_and_opens_no_alert(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc02")
    await db_session.commit()
    report = await _evaluate(
        db_session,
        instance.id,
        [_series(source.id, 4726, NOW, 1)],
        settings=_settings(identity_alerting_enabled=False),
    )
    await db_session.commit()
    assert report.incidents_opened == 1
    assert report.alerts_opened == 0
    assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0
    incident = await db_session.scalar(select(IdentityAnomaly))
    assert incident is not None and incident.state == AnomalyState.OPEN.value


@pytest.mark.parametrize("mode", [AlertPolicyMode.RECORD_ONLY, AlertPolicyMode.DIGEST])
async def test_record_only_and_digest_do_not_page(db_session, mode: AlertPolicyMode) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc03")
    db_session.add(
        AlertPolicy(
            instance_id=instance.id,
            scope_kind=AlertPolicyScope.SOURCE,
            scope_value=str(source.id),
            family=AnomalyFamily.IDENTITY,
            detector_type=DELETED,
            mode=mode,
        )
    )
    await db_session.commit()
    report = await _evaluate(
        db_session, instance.id, [_series(source.id, 4726, NOW, 1)]
    )
    await db_session.commit()
    assert report.incidents_opened == 1
    assert report.alerts_opened == 0
    assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0


async def test_held_suppression_records_a_hit_and_does_not_open(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc04")
    db_session.add(
        AlertSuppression(
            family=AnomalyFamily.IDENTITY,
            subject_id=source.id,
            detector_type=DELETED,
            kind=SuppressionKind.ANALYST,
            reason="planned change",
            owner="analyst",
            starts_at=NOW - timedelta(hours=1),
            ends_at=NOW + timedelta(hours=1),
            created_by="analyst",
        )
    )
    await db_session.commit()
    await _evaluate(db_session, instance.id, [_series(source.id, 4726, NOW, 1)])
    await db_session.commit()
    incident = await db_session.scalar(select(IdentityAnomaly))
    assert incident is not None and incident.suppressed is True
    assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0
    assert await db_session.scalar(select(func.count()).select_from(AlertSuppressionHit)) == 1


async def _band(session, source_id: uuid.UUID, event_id: int, judged: datetime) -> None:
    """Eight exact counts of 1 in the judged hour, inside a 28-day lookback.

    Four weekday matches fall in that window. Two five-minute buckets in the
    same hour on each make one reliable cell. The oldest sits on the lookback
    boundary, so the band is not cold. Counts older than the lookback are not
    loaded.
    """
    for week in range(1, 5):
        start = judged - timedelta(weeks=week)
        for offset in (0, WIDTH):
            session.add(
                IdentityEventCount(
                    log_source_id=source_id,
                    event_id=event_id,
                    bucket_start=start + timedelta(seconds=offset),
                    event_count=1,
                    measurement=EXACT.value,
                    names_measurement=EXACT.value,
                    admission_truncated=False,
                )
            )
    await session.flush()


async def test_a_held_spike_below_ten_stays_quiet_and_ten_breaks_through(db_session) -> None:
    instance = await make_instance(db_session)
    quiet = await make_log_source(db_session, instance, qradar_id=1101, name="quiet-spike")
    loud = await make_log_source(db_session, instance, qradar_id=1102, name="loud-spike")
    for source in (quiet, loud):
        db_session.add(
            AlertSuppression(
                family=AnomalyFamily.IDENTITY,
                subject_id=source.id,
                detector_type=SPIKE,
                kind=SuppressionKind.ANALYST,
                reason="planned change",
                owner="analyst",
                starts_at=NOW - timedelta(hours=1),
                ends_at=NOW + timedelta(days=1),
                created_by="analyst",
            )
        )
        await _band(db_session, source.id, 4625, NOW)
    await db_session.commit()
    settings = _settings(baseline_lookback_days=28, baseline_min_samples=8)

    await _evaluate(
        db_session, instance.id, [_series(quiet.id, 4625, NOW, 4)], settings=settings
    )
    await _evaluate(
        db_session, instance.id, [_series(loud.id, 4625, NOW, 20)], settings=settings
    )
    await db_session.commit()
    alerts = list((await db_session.scalars(select(Alert))).all())
    assert len(alerts) == 1
    assert alerts[0].source_id == loud.id
    assert alerts[0].context["event_id"] == 4625
    quiet_row = await db_session.scalar(
        select(IdentityAnomaly).where(IdentityAnomaly.log_source_id == quiet.id)
    )
    assert quiet_row is not None and quiet_row.suppressed is True


async def test_not_collected_truncated_floor_and_exact_zero_do_not_open(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc05")
    await db_session.commit()
    await _evaluate(
        db_session,
        instance.id,
        [
            _series(source.id, 1102, NOW, None, measurement=MISSING, names_measurement=MISSING),
            _series(source.id, 4720, NOW, 0, measurement=TRUNCATED),
            _series(source.id, 4726, NOW, 0),
        ],
    )
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(IdentityAnomaly)) == 0
    states = {
        row.event_id: row.state
        for row in (await db_session.scalars(select(IdentityDetectorState))).all()
    }
    assert states[1102] == AnomalyState.INSUFFICIENT_DATA.value
    assert states[4720] == AnomalyState.INSUFFICIENT_DATA.value
    assert states[4726] == AnomalyState.NORMAL.value


async def test_new_privileged_name_needs_a_whole_history_and_lists_only_the_new_name(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, name="dc06")
    await db_session.commit()
    width = 7 * 24 * 60 * 60
    history_start = NOW - timedelta(seconds=width)
    missing = await IdentityAnomalyEngine(
        db_session, settings=_settings(), clock=lambda: NOW
    ).evaluate(
        instance_id=instance.id,
        series=[_series(source.id, 4728, NOW, 0, names=(_name(1, "bob"),))],
        bucket_seconds=width,
    )
    await db_session.commit()
    assert missing.insufficient == 1
    assert missing.incidents_opened == 0
    assert await db_session.scalar(select(func.count()).select_from(IdentityAnomaly)) == 0

    db_session.add(
        IdentityBucket(
            log_source_id=source.id,
            bucket_start=history_start,
            bucket_seconds=width,
            catalog_version=1,
            measurement=EXACT.value,
            population=1,
            admission_truncated=False,
            query_provenance={},
            collected_at=history_start,
        )
    )
    db_session.add(
        IdentityEventCount(
            log_source_id=source.id,
            event_id=4728,
            bucket_start=history_start,
            event_count=1,
            measurement=EXACT.value,
            names_measurement=EXACT.value,
            admission_truncated=False,
        )
    )
    db_session.add(
        IdentityTopUsername(
            log_source_id=source.id,
            event_id=4728,
            bucket_start=history_start,
            rank=1,
            username="alice",
            truncated=False,
            event_count=1,
        )
    )
    await db_session.commit()
    # The incomplete tick already advanced every detector on this bucket.
    states = list((await db_session.scalars(select(IdentityDetectorState))).all())
    assert states
    for state in states:
        state.last_interval_start = history_start
    await db_session.commit()

    found = await IdentityAnomalyEngine(
        db_session, settings=_settings(), clock=lambda: NOW
    ).evaluate(
        instance_id=instance.id,
        series=[
            _series(
                source.id,
                4728,
                NOW,
                2,
                names=(_name(1, "alice", 1), _name(2, "bob", 1)),
            )
        ],
        bucket_seconds=width,
    )
    await db_session.commit()
    incident = await db_session.scalar(
        select(IdentityAnomaly).where(IdentityAnomaly.detector_type == NOVEL)
    )
    assert found.incidents_opened == 2
    assert incident is not None
    assert incident.details["usernames"] == [{"rank": 2, "username": "bob", "count": 1}]
    assert "bob" not in (incident.explanation or "")


async def test_statement_count_stays_constant_as_sources_and_names_grow(db_session) -> None:
    instance = await make_instance(db_session)
    one = await make_log_source(db_session, instance, qradar_id=1201, name="one")
    many = [
        await make_log_source(db_session, instance, qradar_id=1300 + index, name=f"s{index}")
        for index in range(5)
    ]
    await db_session.commit()

    def payload(source_id: uuid.UUID, names: int) -> IdentitySeries:
        stored = tuple(_name(rank, f"user{rank:02d}") for rank in range(1, names + 1))
        return _series(source_id, 4726, NOW, 3, names=stored)

    small_engine = IdentityAnomalyEngine(db_session, settings=_settings(), clock=lambda: NOW)
    with _counting(db_session) as small_sql:
        small = await small_engine.evaluate(
            instance_id=instance.id, series=[payload(one.id, 1)], bucket_seconds=WIDTH
        )
    large_engine = IdentityAnomalyEngine(db_session, settings=_settings(), clock=lambda: NOW)
    with _counting(db_session) as large_sql:
        large = await large_engine.evaluate(
            instance_id=instance.id,
            series=[payload(source.id, 20) for source in many],
            bucket_seconds=WIDTH,
        )
    assert small.incidents_opened == 1
    assert large.incidents_opened == 5
    assert len(small_sql) == len(large_sql)
    assert len(small_sql) > 0
