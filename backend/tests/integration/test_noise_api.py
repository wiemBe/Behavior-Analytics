"""The noisy-sources routes against a real database (SOC-17a, design §2).

These tests pin the two reads the list rests on:

* the honesty states -- not collected, not judged, partial -- with the exact
  nullability each requires;
* the counts, including the NOISE denominator (an unclosed incident is not a
  negative judgement) and the alert link through ``source_anomaly_ids``;
* the SQL cost: three statements at two fleet sizes, and the same rows a
  per-source calculation produces.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert
from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    ClassificationReviewStatus,
    Criticality,
    Severity,
    SourceClass,
    TriageDisposition,
    TriageStatus,
)
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly, LogSourceBaseline
from app.models.monitoring import AnomalyEvaluationWatermark
from app.models.triage import AnomalyTriage
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the noise API tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
ALERTS_ONLY = Principal(subject="alerts", permissions=frozenset({"alert:ack"}))

#: Three statements: sources, incident groups, alert groups.
LIST_STATEMENTS = 3


def _stable_now() -> datetime:
    """One clock read that will still fall in the same UTC hour after seeding.

    The baseline cell is keyed by (weekday, hour), so a test that seeds at
    18:59:59 and requests at 19:00:01 would judge a different cell. The wait is
    bounded by the guard and only happens just before the hour turns.
    """
    now = datetime.now(UTC)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    remaining = (next_hour - now).total_seconds()
    if remaining <= 60:
        time.sleep(remaining + 0.2)
        now = datetime.now(UTC)
    return now.replace(microsecond=0)


@pytest.fixture
def clock() -> datetime:
    return _stable_now()


@contextmanager
def _recorded_statements(session: Any):
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def reader_client(
    db_url: str,
    *,
    principal: Principal = READER,
    settings: Settings | None = None,
) -> TestClient:
    """A client on its own engine, so a test can count the statements it issues."""
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    api = TestClient(app)
    api.engine = engine  # type: ignore[attr-defined]
    return api


async def _add_source(
    session,
    instance: QRadarInstance,
    *,
    name: str,
    qradar_id: int,
    now: datetime,
    monitoring: bool = True,
    reliable: bool | None = True,
    source_class: SourceClass | None = SourceClass.FIREWALL,
) -> LogSource:
    source = LogSource(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=name,
        monitoring_enabled=monitoring,
    )
    session.add(source)
    await session.flush()
    if reliable is not None:
        session.add(
            LogSourceBaseline(
                log_source_id=source.id,
                metric_name="average_eps",
                weekday=now.isoweekday(),
                hour=now.hour,
                median=10.0,
                is_reliable=reliable,
            )
        )
    if source_class is not None:
        session.add(
            LogSourceClassification(
                log_source_id=source.id,
                source_class=source_class,
                basis="test",
                classified_at=now,
                review_status=ClassificationReviewStatus.NEW,
            )
        )
    session.add(
        AnomalyEvaluationWatermark(
            log_source_id=source.id, evaluated_through=now - timedelta(minutes=5)
        )
    )
    await session.flush()
    return source


async def _add_incident(
    session,
    source: LogSource,
    *,
    detected_at: datetime,
    kind: AnomalyType = AnomalyType.VOLUME_SPIKE,
    opened: bool = False,
    ratio: float | None = None,
    z: float | None = None,
    triage_status: TriageStatus | None = None,
    disposition: TriageDisposition | None = None,
) -> LogSourceAnomaly:
    incident = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=kind,
        severity=Severity.LOW,
        state=AnomalyState.RESOLVED,
        detected_at=detected_at,
        opened_at=detected_at if opened else None,
        deviation_ratio=ratio,
        robust_z=z,
    )
    session.add(incident)
    await session.flush()
    if triage_status is not None or disposition is not None:
        session.add(
            AnomalyTriage(
                family=AnomalyFamily.LOG_SOURCE.value,
                anomaly_id=incident.id,
                status=triage_status or TriageStatus.CLOSED,
                disposition=disposition,
                updated_by="tester",
                version=1,
            )
        )
        await session.flush()
    return incident


def _alert(
    source: LogSource,
    anomaly_ids: list[uuid.UUID],
    *,
    opened_at: datetime,
    title: str = "anomaly alert",
) -> Alert:
    token = uuid.uuid4().hex
    return Alert(
        dedup_key=f"dedup-{token}",
        fingerprint=f"fingerprint-{token}",
        title=title,
        severity=Severity.LOW,
        source_type="log_source",
        source_id=source.id,
        source_anomaly_ids=[str(item) for item in anomaly_ids],
        opened_at=opened_at,
        first_seen_at=opened_at,
    )


async def _instance(session) -> QRadarInstance:
    instance = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(instance)
    await session.flush()
    return instance


# ------------------------------------------------------------------ authorization
class TestAuthorization:
    @pytest.mark.parametrize(
        "path",
        ["/api/v1/noise/sources", f"/api/v1/noise/sources/{uuid.uuid4()}"],
    )
    def test_a_principal_without_the_read_permission_is_refused(
        self, path: str, db_schema
    ) -> None:
        assert reader_client(db_schema, principal=NOBODY).get(path).status_code == 403

    def test_an_unrelated_permission_does_not_grant_access(self, db_schema) -> None:
        client = reader_client(db_schema, principal=ALERTS_ONLY)
        assert client.get("/api/v1/noise/sources").status_code == 403
        assert client.get(f"/api/v1/noise/sources/{uuid.uuid4()}").status_code == 403

    def test_the_read_wildcard_and_admin_retain_access(self, db_schema) -> None:
        reader = reader_client(db_schema, principal=READER)
        admin = reader_client(db_schema, principal=ADMIN)
        assert reader.get("/api/v1/noise/sources").status_code == 200
        assert admin.get("/api/v1/noise/sources").status_code == 200

    def test_a_denied_detail_request_does_not_reveal_existence(self, db_schema) -> None:
        client = reader_client(db_schema, principal=NOBODY)
        assert client.get(f"/api/v1/noise/sources/{uuid.uuid4()}").status_code == 403


# --------------------------------------------------------------------------- errors
class TestRequestValidation:
    def test_an_unknown_source_is_404(self, db_schema) -> None:
        response = reader_client(db_schema).get(f"/api/v1/noise/sources/{uuid.uuid4()}")
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "params",
        [
            {"window": 30},
            {"window": 0},
            {"order": "noise"},
            {"limit": 0},
            {"limit": 201},
            {"offset": -1},
        ],
    )
    def test_invalid_queries_are_422(self, params: dict, db_schema) -> None:
        response = reader_client(db_schema).get("/api/v1/noise/sources", params=params)
        assert response.status_code == 422

    def test_a_malformed_source_id_is_422(self, db_schema) -> None:
        assert reader_client(db_schema).get("/api/v1/noise/sources/not-a-uuid").status_code == 422


# --------------------------------------------------------------------------- windows
async def test_the_window_selects_the_counts_and_the_membership(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="windowed", qradar_id=1, now=clock)
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=2))
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=20))
    # Outside both windows: it must not appear in either count.
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=40))
    await db_session.commit()

    body = reader_client(db_schema).get("/api/v1/noise/sources", params={"window": 7}).json()
    row = body["items"][0]
    assert row["incidents_7d"] == 1
    assert row["incidents_28d"] == 2
    assert body["window_days"] == 7

    body = reader_client(db_schema).get("/api/v1/noise/sources", params={"window": 28}).json()
    assert body["items"][0]["incidents_7d"] == 1
    assert body["items"][0]["incidents_28d"] == 2


async def test_a_source_with_no_incident_in_the_selected_window_is_quiet(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="quiet", qradar_id=2, now=clock)
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=20))
    await db_session.commit()

    body = reader_client(db_schema).get("/api/v1/noise/sources", params={"window": 7}).json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["zero_fired"] == 1
    assert body["empty_reason"]["code"] == "noise.reason.empty_window"


# ------------------------------------------------------------------------- noise share
async def test_a_share_needs_five_closed_dispositions(db_session, db_schema, clock) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="four", qradar_id=3, now=clock)
    for index in range(4):
        await _add_incident(
            db_session,
            source,
            detected_at=clock - timedelta(hours=index + 1),
            triage_status=TriageStatus.CLOSED,
            disposition=TriageDisposition.NOISE,
        )
    await db_session.commit()

    row = reader_client(db_schema).get("/api/v1/noise/sources").json()["items"][0]
    assert row["noise_closed"] == 4
    assert row["noise_closed_total"] == 4
    assert row["noise_share"] is None


async def test_five_closed_dispositions_produce_the_share(db_session, db_schema, clock) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="five", qradar_id=4, now=clock)
    for index in range(4):
        await _add_incident(
            db_session,
            source,
            detected_at=clock - timedelta(hours=index + 1),
            triage_status=TriageStatus.CLOSED,
            disposition=TriageDisposition.NOISE,
        )
    await _add_incident(
        db_session,
        source,
        detected_at=clock - timedelta(hours=6),
        triage_status=TriageStatus.CLOSED,
        disposition=TriageDisposition.TRUE_POSITIVE,
    )
    await db_session.commit()

    row = reader_client(db_schema).get("/api/v1/noise/sources").json()["items"][0]
    assert row["noise_closed"] == 4
    assert row["noise_closed_total"] == 5
    assert row["noise_share"] == pytest.approx(0.8)


async def test_an_unclosed_incident_is_not_a_negative_judgement(
    db_session, db_schema, clock
) -> None:
    """Four closed dispositions plus one open item: the denominator stays four."""
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="denominator", qradar_id=5, now=clock)
    for index in range(4):
        await _add_incident(
            db_session,
            source,
            detected_at=clock - timedelta(hours=index + 1),
            triage_status=TriageStatus.CLOSED,
            disposition=TriageDisposition.NOISE,
        )
    # Untouched incident: no triage row at all.
    await _add_incident(db_session, source, detected_at=clock - timedelta(hours=5))
    # In progress, with a disposition written before it closed: not a judgement yet.
    await _add_incident(
        db_session,
        source,
        detected_at=clock - timedelta(hours=6),
        triage_status=TriageStatus.IN_PROGRESS,
        disposition=TriageDisposition.NOISE,
    )
    await db_session.commit()

    row = reader_client(db_schema).get("/api/v1/noise/sources").json()["items"][0]
    assert row["incidents_28d"] == 6
    assert row["noise_closed"] == 4
    assert row["noise_closed_total"] == 4
    assert row["noise_share"] is None


# ---------------------------------------------------------------------------- alerts
async def test_alerts_count_through_source_anomaly_ids_only(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="alerts", qradar_id=6, now=clock)
    incident = await _add_incident(db_session, source, detected_at=clock - timedelta(days=2))
    db_session.add(
        _alert(source, [incident.id], opened_at=clock - timedelta(days=2))
    )
    # An inventory alert is a log_source alert with no anomaly: it must not count.
    db_session.add(_alert(source, [], opened_at=clock - timedelta(days=2), title="inventory"))
    await db_session.commit()

    row = reader_client(db_schema).get("/api/v1/noise/sources").json()["items"][0]
    assert row["alerts_7d"] == 1
    assert row["alerts_28d"] == 1


async def test_opened_counts_only_incidents_that_reached_open(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="opened", qradar_id=7, now=clock)
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=1), opened=True)
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=2), opened=False)
    await db_session.commit()

    row = reader_client(db_schema).get("/api/v1/noise/sources").json()["items"][0]
    assert row["incidents_7d"] == 2
    assert row["opened_7d"] == 1


# -------------------------------------------------------------------- honesty states
async def test_a_not_monitored_source_is_not_collected_with_null_counts(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(
        db_session,
        instance,
        name="unmonitored",
        qradar_id=8,
        now=clock,
        monitoring=False,
    )
    incident = await _add_incident(db_session, source, detected_at=clock - timedelta(days=1))
    db_session.add(_alert(source, [incident.id], opened_at=clock - timedelta(days=1)))
    await db_session.commit()

    body = reader_client(db_schema).get(f"/api/v1/noise/sources/{source.id}").json()
    assert body["judgement"] == "NOT_COLLECTED"
    assert body["judged"] is False
    assert body["reason"]["code"] == "noise.reason.not_monitored"
    for field in (
        "incidents_7d",
        "incidents_28d",
        "opened_7d",
        "opened_28d",
        "alerts_7d",
        "alerts_28d",
        "noise_closed",
        "noise_closed_total",
        "noise_share",
        "worst_ratio",
        "worst_z",
    ):
        assert body[field] is None, field
    assert body["detectors"] == []

    # The list is the monitored fleet only; the unmonitored source is absent.
    listing = reader_client(db_schema).get("/api/v1/noise/sources").json()
    assert listing["items"] == []
    assert listing["zero_fired"] == 0


async def test_a_monitored_source_without_a_reliable_cell_is_not_judged(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(
        db_session, instance, name="unjudged", qradar_id=9, now=clock, reliable=None
    )
    await db_session.commit()

    body = reader_client(db_schema).get(f"/api/v1/noise/sources/{source.id}").json()
    assert body["judgement"] == "INSUFFICIENT_DATA"
    assert body["judged"] is False
    assert body["reason"]["code"] == "noise.reason.no_reliable_baseline"
    # A recorded zero is not null: the counts exist, the verdict does not.
    assert body["incidents_28d"] == 0
    assert body["alerts_28d"] == 0

    listing = reader_client(db_schema).get("/api/v1/noise/sources").json()
    assert listing["zero_fired"] == 1
    assert listing["zero_fired_judged"] == 0
    assert listing["zero_fired_insufficient_data"] == 1


async def test_an_unreliable_cell_is_not_judged(db_session, db_schema, clock) -> None:
    instance = await _instance(db_session)
    source = await _add_source(
        db_session, instance, name="weak", qradar_id=10, now=clock, reliable=False
    )
    await db_session.commit()

    body = reader_client(db_schema).get(f"/api/v1/noise/sources/{source.id}").json()
    assert body["judgement"] == "INSUFFICIENT_DATA"


async def test_partial_coverage_follows_the_configured_retention(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="partial", qradar_id=11, now=clock)
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=2))
    await db_session.commit()

    settings = get_settings().model_copy(update={"log_source_anomaly_retention_days": 7})
    body = reader_client(db_schema, settings=settings).get(
        "/api/v1/noise/sources", params={"window": 28}
    ).json()
    assert body["coverage"]["state"] == "PARTIAL"
    assert body["coverage"]["covered_days"] == 7
    assert body["coverage"]["window_days"] == 28
    assert body["coverage"]["retention_days"] == 7
    assert body["coverage"]["reason"]["code"] == "noise.reason.partial_retention"

    short = reader_client(db_schema, settings=settings).get(
        "/api/v1/noise/sources", params={"window": 7}
    ).json()
    assert short["coverage"]["state"] == "COMPLETE"


# ------------------------------------------------------------------------- detail
async def test_detail_reports_both_windows_per_detector(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="detail", qradar_id=12, now=clock)
    first = await _add_incident(
        db_session, source, detected_at=clock - timedelta(days=1), kind=AnomalyType.VOLUME_SPIKE
    )
    await _add_incident(
        db_session, source, detected_at=clock - timedelta(days=3), kind=AnomalyType.VOLUME_SPIKE
    )
    await _add_incident(
        db_session, source, detected_at=clock - timedelta(days=20), kind=AnomalyType.NO_EVENTS
    )
    db_session.add(_alert(source, [first.id], opened_at=clock - timedelta(days=1)))
    await db_session.commit()

    body = reader_client(db_schema).get(f"/api/v1/noise/sources/{source.id}").json()
    assert body["incidents_7d"] == 2
    assert body["incidents_28d"] == 3
    assert body["top_detector"] == "VOLUME_SPIKE"
    by_type = {item["anomaly_type"]: item for item in body["detectors"]}
    assert set(by_type) == {"VOLUME_SPIKE", "NO_EVENTS"}
    assert by_type["VOLUME_SPIKE"]["incidents_7d"] == 2
    assert by_type["VOLUME_SPIKE"]["alerts_7d"] == 1
    assert by_type["NO_EVENTS"]["incidents_7d"] == 0
    assert body["current"]["criticality"] == "MEDIUM"
    assert body["suppression"] is None
    assert body["replay"] is None


async def test_detail_reports_the_effective_thresholds_and_their_source(
    db_session, db_schema, clock
) -> None:
    """The tuning form pre-fills from the resolver, so the detail route says so.

    Default and custom are represented here; criticality and LAB_MODE are the
    two tests below. ``by_type`` must not move the top-level value (design §3.2).
    """
    instance = await _instance(db_session)
    source = await _add_source(
        db_session, instance, name="thresholds", qradar_id=20, now=clock
    )
    source.criticality = Criticality.LOW
    source.custom_thresholds = {
        "open_after": 4,
        "direction": "spike",
        "by_type": {"VOLUME_SPIKE": {"deviation_z": 9.0}},
    }
    await db_session.commit()

    body = reader_client(
        db_schema, settings=Settings(_env_file=None, debug=False)
    ).get(f"/api/v1/noise/sources/{source.id}").json()

    current = body["current"]
    assert current["lab_mode"] is False
    assert current["has_by_type"] is True
    assert current["effective_thresholds"] == {
        "deviation_z": {"value": 3.5, "source": "default"},
        "spike_ratio": {"value": 2.0, "source": "default"},
        "drop_ratio": {"value": 0.5, "source": "default"},
        "min_absolute_delta_events": {"value": 100.0, "source": "default"},
        "open_after": {"value": 4, "source": "custom"},
        "direction": {"value": "spike", "source": "custom"},
    }


async def test_detail_names_criticality_when_it_supplies_open_after(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(
        db_session, instance, name="criticality", qradar_id=21, now=clock
    )
    source.criticality = Criticality.CRITICAL
    await db_session.commit()

    body = reader_client(
        db_schema, settings=Settings(_env_file=None, debug=False)
    ).get(f"/api/v1/noise/sources/{source.id}").json()

    current = body["current"]
    assert current["effective_thresholds"]["open_after"] == {
        "value": 1,
        "source": "criticality",
    }
    assert current["has_by_type"] is False
    assert current["lab_mode"] is False


async def test_detail_names_lab_mode_and_keeps_detection_knobs_production(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    source = await _add_source(
        db_session, instance, name="lab", qradar_id=22, now=clock
    )
    source.criticality = Criticality.LOW
    await db_session.commit()

    body = reader_client(
        db_schema, settings=Settings(_env_file=None, debug=False, lab_mode=True)
    ).get(f"/api/v1/noise/sources/{source.id}").json()

    current = body["current"]
    assert current["lab_mode"] is True
    assert current["effective_thresholds"]["open_after"] == {
        "value": 1,
        "source": "lab_mode",
    }
    # LAB_MODE never lowers a detection threshold.
    assert current["effective_thresholds"]["deviation_z"] == {
        "value": 3.5,
        "source": "default",
    }


async def test_detail_passes_a_malformed_stored_override_through(
    db_session, db_schema, clock
) -> None:
    """A row that never passed validation is read, not refused (r0's rule)."""
    instance = await _instance(db_session)
    source = await _add_source(
        db_session, instance, name="malformed", qradar_id=23, now=clock
    )
    source.custom_thresholds = {"deviation_z": None, "direction": "sideways"}
    await db_session.commit()

    response = reader_client(
        db_schema, settings=Settings(_env_file=None, debug=False)
    ).get(f"/api/v1/noise/sources/{source.id}")
    assert response.status_code == 200, response.text
    thresholds = response.json()["current"]["effective_thresholds"]
    assert thresholds["deviation_z"] == {"value": None, "source": "custom"}
    assert thresholds["direction"] == {"value": "sideways", "source": "custom"}


# ------------------------------------------------------------------- response shape
LIST_KEYS = {
    "window_days",
    "order",
    "limit",
    "offset",
    "total",
    "zero_fired",
    "zero_fired_judged",
    "zero_fired_insufficient_data",
    "last_collection",
    "coverage",
    "empty_reason",
    "items",
}
SOURCE_KEYS = {
    "log_source_id",
    "name",
    "instance_id",
    "instance_name",
    "criticality",
    "source_class",
    "monitoring_enabled",
    "judgement",
    "judged",
    "reason",
    "incidents_7d",
    "incidents_28d",
    "opened_7d",
    "opened_28d",
    "alerts_7d",
    "alerts_28d",
    "noise_closed",
    "noise_closed_total",
    "noise_share",
    "worst_ratio",
    "worst_z",
    "top_detector",
    "evaluated_through",
}
DETECTOR_KEYS = {
    "anomaly_type",
    "incidents_7d",
    "incidents_28d",
    "opened_7d",
    "opened_28d",
    "alerts_7d",
    "alerts_28d",
    "noise_closed",
    "noise_closed_total",
    "noise_share",
    "worst_ratio",
    "worst_z",
}
COVERAGE_KEYS = {"state", "window_days", "covered_days", "retention_days", "reason"}


async def test_the_response_key_sets_are_stable(db_session, db_schema, clock) -> None:
    instance = await _instance(db_session)
    source = await _add_source(db_session, instance, name="shape", qradar_id=13, now=clock)
    await _add_incident(db_session, source, detected_at=clock - timedelta(days=1))
    await db_session.commit()

    listing = reader_client(db_schema).get("/api/v1/noise/sources").json()
    assert set(listing) == LIST_KEYS
    assert set(listing["coverage"]) == COVERAGE_KEYS
    assert set(listing["items"][0]) == SOURCE_KEYS

    detail = reader_client(db_schema).get(f"/api/v1/noise/sources/{source.id}").json()
    assert set(detail) == SOURCE_KEYS | {
        "detectors",
        "current",
        "coverage",
        "suppression",
        "replay",
    }
    assert set(detail["detectors"][0]) == DETECTOR_KEYS
    assert set(detail["current"]) == {
        "criticality",
        "custom_thresholds",
        "maintenance_mode",
        "maintenance_until",
        "business_hours_only",
        "expected_interval_seconds",
        "effective_thresholds",
        "lab_mode",
        "has_by_type",
    }
    assert set(detail["current"]["effective_thresholds"]) == {
        "deviation_z",
        "spike_ratio",
        "drop_ratio",
        "min_absolute_delta_events",
        "open_after",
        "direction",
    }
    assert set(detail["current"]["effective_thresholds"]["open_after"]) == {
        "value",
        "source",
    }


# ---------------------------------------------------------------------------- cost
async def test_the_list_costs_three_statements_at_two_fleet_sizes(
    db_session, db_schema, clock
) -> None:
    instance = await _instance(db_session)
    first = await _add_source(db_session, instance, name="cost-01", qradar_id=14, now=clock)
    first_incident = await _add_incident(
        db_session, first, detected_at=clock - timedelta(days=1), opened=True
    )
    await _add_incident(db_session, first, detected_at=clock - timedelta(days=20))
    db_session.add(_alert(first, [first_incident.id], opened_at=clock - timedelta(days=1)))
    second = await _add_source(db_session, instance, name="cost-02", qradar_id=15, now=clock)
    await _add_incident(db_session, second, detected_at=clock - timedelta(days=2))
    await db_session.commit()

    api = reader_client(db_schema)
    # Warm every lazy import and the statement cache, then measure.
    assert api.get("/api/v1/noise/sources").status_code == 200
    async with async_sessionmaker(api.engine, expire_on_commit=False)() as anchor:  # type: ignore[attr-defined]
        with _recorded_statements(anchor) as statements:
            response = api.get("/api/v1/noise/sources")
        assert response.status_code == 200
        assert len(statements) == LIST_STATEMENTS, statements

        expected = {
            "cost-01": (1, 2, 1),
            "cost-02": (1, 1, 0),
        }
        rows = {item["name"]: item for item in response.json()["items"]}
        assert set(rows) == set(expected)
        for name, (incidents_7d, incidents_28d, alerts_28d) in expected.items():
            assert rows[name]["incidents_7d"] == incidents_7d, name
            assert rows[name]["incidents_28d"] == incidents_28d, name
            assert rows[name]["alerts_28d"] == alerts_28d, name

        # Four more sources, each with an incident and one with an alert.
        for index in range(4):
            source = await _add_source(
                db_session,
                instance,
                name=f"cost-{index + 3:02d}",
                qradar_id=16 + index,
                now=clock,
            )
            incident = await _add_incident(
                db_session, source, detected_at=clock - timedelta(days=1)
            )
            if index == 0:
                db_session.add(
                    _alert(source, [incident.id], opened_at=clock - timedelta(days=1))
                )
        await db_session.commit()

        with _recorded_statements(anchor) as statements:
            response = api.get("/api/v1/noise/sources")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 6
        assert len(statements) == LIST_STATEMENTS, statements

        # Row equality: each list row equals the scoped per-source calculation.
        list_rows = {item["log_source_id"]: item for item in response.json()["items"]}
        count_keys = (
            "incidents_7d",
            "incidents_28d",
            "opened_7d",
            "opened_28d",
            "alerts_7d",
            "alerts_28d",
            "noise_closed",
            "noise_closed_total",
            "noise_share",
            "worst_ratio",
            "top_detector",
            "judgement",
        )
        for source_id, row in list_rows.items():
            scoped = api.get(f"/api/v1/noise/sources/{source_id}").json()
            for key in count_keys:
                assert scoped[key] == row[key], (row["name"], key)

        # The detail route uses the same three reads, scoped to one source.
        with _recorded_statements(anchor) as statements:
            response = api.get(f"/api/v1/noise/sources/{first.id}")
        assert response.status_code == 200
        assert len(statements) == LIST_STATEMENTS, statements
