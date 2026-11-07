"""The 28-day backtest preview (SOC-17b, design §3.3-§3.5).

The tests pin three contracts:

* the replay equals the engine: the real engine is driven over a fixture window
  and the replay of the same window, with the same thresholds, opens the same
  incidents per detector from a clean NORMAL;
* every fail-closed exit -- COMPLETE, PARTIAL, INSUFFICIENT_DATA (short coverage,
  too many unjudged buckets, maintenance), TOO_LARGE -- returns null counts and
  never zero;
* the cost is four statements whatever the fleet size, and the route keeps its
  `read:anomalies` permission and its read-only shape.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.anomaly.engine import AnomalyEngine
from app.anomaly.thresholds import ThresholdResolver
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.custom_rules.preview import latest_closed_bucket
from app.main import create_app
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Criticality,
    OutagePolicyMode,
    Severity,
)
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly, LogSourceBaseline
from app.noise.backtest import NoiseBacktest, ReplayState, _thresholds
from app.schemas.noise import preview_out
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import add_metric

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
BUCKET = 3600
DAYS = 7
BUCKETS = DAYS * 24
#: The preview's four statements: source, metrics, cells, incidents.
PREVIEW_STATEMENTS = 4

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
ALERTS_ONLY = Principal(subject="alerts", permissions=frozenset({"alert:ack"}))
WRITER = Principal(
    subject="writer", permissions=frozenset({"read:*", "log_source:write"})
)


def _settings(**kwargs) -> Settings:
    values = {"encryption_key": "x" * 44}
    values.update(kwargs)
    return Settings(**values)


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
    app.dependency_overrides[get_principal] = lambda: principal
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    client.engine = engine  # type: ignore[attr-defined]
    return client


async def _instance(session) -> QRadarInstance:
    instance = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(instance)
    await session.flush()
    return instance


async def _source(
    session,
    instance: QRadarInstance,
    *,
    qradar_id: int = 1,
    expected_interval_seconds: int | None = BUCKET,
    **kwargs,
) -> LogSource:
    source = LogSource(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=f"src-{qradar_id}",
        expected_interval_seconds=expected_interval_seconds,
        **kwargs,
    )
    session.add(source)
    await session.flush()
    return source


async def _reliable_cells(session, source: LogSource, *, median: float = 50.0) -> None:
    """One reliable average_eps and event_count cell for every weekday/hour."""
    for weekday in range(1, 8):
        for hour in range(24):
            for metric, value, mad in (
                ("average_eps", median, 2.0),
                ("event_count", median * BUCKET, 500.0),
            ):
                session.add(
                    LogSourceBaseline(
                        log_source_id=source.id,
                        metric_name=metric,
                        weekday=weekday,
                        hour=hour,
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


async def _metric(session, source, bucket: datetime, *, eps: float):
    return await add_metric(
        session,
        source,
        bucket,
        average_eps=eps,
        event_count=int(eps * BUCKET),
        bucket_seconds=BUCKET,
        completeness=BucketCompleteness.COMPLETE,
        payload_signature=f"sig-{bucket.isoformat()}",
    )


async def _fill(
    session,
    source,
    *,
    count: int = BUCKETS,
    spike_at: int | None = None,
    drop_at: int | None = None,
    start: datetime = START,
    spike_length: int = 3,
    drop_length: int = 3,
) -> list:
    """Baseline-volume buckets, with one short spike and/or drop episode."""
    metrics = []
    for index in range(count):
        eps = 50.0
        if spike_at is not None and spike_at <= index < spike_at + spike_length:
            eps = 150.0
        if drop_at is not None and drop_at <= index < drop_at + drop_length:
            eps = 5.0
        metrics.append(await _metric(session, source, start + timedelta(hours=index), eps=eps))
    return metrics


async def _drive_engine(session, source, metrics, *, settings: Settings | None = None):
    engine = AnomalyEngine(session, settings=settings or _settings())
    opened: list[str] = []
    for metric in metrics:
        report = await engine.evaluate_interval(source, metric)
        opened.extend(report.opened)
    return opened


async def _replay(
    session,
    source,
    *,
    window_days: int = DAYS,
    proposed: dict | None = None,
    now: datetime | None = None,
    criticality: Criticality | None = None,
    settings: Settings | None = None,
):
    backtest = NoiseBacktest(
        session,
        settings=settings or _settings(),
        now=now or START + timedelta(days=window_days),
    )
    return await backtest.preview(
        source.id,
        proposed=proposed or {},
        window_days=window_days,
        criticality=criticality,
    )


def _counts(result) -> dict[str, int]:  # type: ignore[no-untyped-def]
    assert result.current is not None
    return {item.anomaly_type.value: item.incidents for item in result.current.by_detector}


# ------------------------------------------------------------- replay equals engine
async def test_the_replay_opens_what_the_engine_opened(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    metrics = await _fill(db_session, source, spike_at=20, drop_at=100)
    opened = await _drive_engine(db_session, source, metrics)
    assert sorted(opened) == ["VOLUME_DROP", "VOLUME_SPIKE"]

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.COMPLETE
    counts = _counts(result)
    assert counts["VOLUME_SPIKE"] == opened.count("VOLUME_SPIKE") == 1
    assert counts["VOLUME_DROP"] == opened.count("VOLUME_DROP") == 1
    # The recorded history is a different number from the replay's M.
    assert result.recorded is not None
    assert result.recorded.incidents == 2
    assert result.recorded.opened == 2


async def test_the_same_window_with_the_same_thresholds_gives_the_same_n(
    db_session,
) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    metrics = await _fill(db_session, source, spike_at=20)
    await _drive_engine(db_session, source, metrics)

    unchanged = await _replay(db_session, source, proposed={})
    assert unchanged is not None
    assert unchanged.current is not None and unchanged.proposed is not None
    assert unchanged.current.total_incidents == unchanged.proposed.total_incidents
    assert unchanged.current.by_detector == unchanged.proposed.by_detector


async def test_by_type_detector_thresholds_stay_inert(db_session) -> None:
    """`by_type` does not reach the detector today, and 17b does not change that."""
    instance = await _instance(db_session)
    source = await _source(
        db_session,
        instance,
        custom_thresholds={"by_type": {"VOLUME_SPIKE": {"deviation_z": 1000}}},
    )
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)

    unchanged = await _replay(db_session, source)
    assert unchanged is not None
    assert _counts(unchanged)["VOLUME_SPIKE"] == 1

    # A top-level value does reach it, which is what makes the by_type case inert
    # rather than the replay ignoring thresholds altogether.
    raised = await _replay(db_session, source, proposed={"spike_ratio": 10})
    assert raised is not None and raised.proposed is not None
    assert {
        item.anomaly_type.value: item.incidents for item in raised.proposed.by_detector
    }["VOLUME_SPIKE"] == 0


def _rows(counts) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {item.anomaly_type.value: item for item in counts.by_detector}


async def test_a_proposed_direction_excludes_with_null_and_a_flag_not_zero(
    db_session,
) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20, drop_at=100)

    result = await _replay(db_session, source, proposed={"direction": "spike"})
    assert result is not None
    assert result.current is not None and result.proposed is not None
    current = _rows(result.current)
    proposed = _rows(result.proposed)

    # The current rule runs both directions, so the drop is a real measurement.
    assert current["VOLUME_DROP"].incidents == 1
    assert current["VOLUME_DROP"].excluded_by_direction is False
    assert current["VOLUME_SPIKE"].incidents == 1

    # The proposed rule skips the drop detector: null + flag, never a zero.
    assert proposed["VOLUME_DROP"].incidents is None
    assert proposed["VOLUME_DROP"].excluded_by_direction is True
    assert proposed["VOLUME_SPIKE"].incidents == 1
    assert result.proposed.total_incidents == 1
    assert result.proposed.total_incidents == sum(
        item.incidents
        for item in result.proposed.by_detector
        if item.incidents is not None
    )


async def test_the_other_proposed_direction_is_symmetric(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20, drop_at=100)

    result = await _replay(db_session, source, proposed={"direction": "drop"})
    assert result is not None and result.proposed is not None
    proposed = _rows(result.proposed)

    assert proposed["VOLUME_SPIKE"].incidents is None
    assert proposed["VOLUME_SPIKE"].excluded_by_direction is True
    assert proposed["VOLUME_DROP"].incidents == 1
    assert result.proposed.total_incidents == 1


async def test_a_current_direction_excludes_the_same_way_and_is_replayable_after(
    db_session,
) -> None:
    """A stored direction also nulls its row, and the proposed run still reports it.

    The proposed fold's own judged set is used: when the current rule disables
    drop but the proposal enables both, drop must come back with a real count.
    """
    instance = await _instance(db_session)
    source = await _source(
        db_session, instance, custom_thresholds={"direction": "spike"}
    )
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20, drop_at=100)

    result = await _replay(db_session, source, proposed={"direction": "both"})
    assert result is not None
    assert result.current is not None and result.proposed is not None
    current = _rows(result.current)
    proposed = _rows(result.proposed)

    assert current["VOLUME_DROP"].incidents is None
    assert current["VOLUME_DROP"].excluded_by_direction is True
    assert current["VOLUME_SPIKE"].incidents == 1
    assert result.current.total_incidents == 1
    assert [
        item.anomaly_type.value
        for item in result.not_replayable
        if item.reason.code == "noise.preview.direction_disabled"
    ] == ["VOLUME_DROP"]

    # The proposal re-enables both directions, so drop runs again.
    assert proposed["VOLUME_DROP"].incidents == 1
    assert proposed["VOLUME_DROP"].excluded_by_direction is False
    assert result.proposed.total_incidents == 2


# --------------------------------------------------------------------- the states
async def test_a_complete_window_states_its_counts(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.COMPLETE
    assert result.available is True
    assert result.window.days == 7
    assert result.window.covered_days == 7
    assert result.window.first_bucket == START + timedelta(hours=1)
    assert result.window.last_bucket == START + timedelta(hours=BUCKETS - 1)
    assert result.not_judged_buckets == 0
    assert result.reason is None
    assert result.baseline.baseline_version == 3
    assert result.baseline.computed_at is not None
    assert result.current is not None and result.proposed is not None


async def test_a_partial_window_labels_the_covered_span(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, count=14 * 24)

    result = await _replay(
        db_session, source, window_days=28, now=START + timedelta(days=14)
    )
    assert result is not None
    assert result.state is ReplayState.PARTIAL
    assert result.available is True
    assert result.window.covered_days == 14
    assert result.window.days == 28
    assert result.current is not None and result.proposed is not None
    assert result.reason is not None
    assert result.reason.code == "noise.preview.partial"


async def test_fewer_than_seven_covered_days_is_insufficient(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, count=5 * 24)

    result = await _replay(
        db_session, source, window_days=7, now=START + timedelta(days=5)
    )
    assert result is not None
    assert result.state is ReplayState.INSUFFICIENT_DATA
    assert result.available is False
    assert result.window.covered_days == 5
    assert result.current is None and result.proposed is None
    assert result.reason is not None
    assert result.reason.code == "noise.preview.insufficient_coverage"


async def test_a_missing_bucket_is_insufficient_and_never_a_smaller_count(
    db_session,
) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    metrics = await _fill(db_session, source, spike_at=20)
    # Drop 20 buckets from the middle of the window: 12 %, past the 10 % a
    # count may rest on. Deleting from the start would look like "source first
    # seen" and shrink the covered span instead.
    for metric in metrics[50:70]:
        await db_session.delete(metric)
    await db_session.flush()

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.INSUFFICIENT_DATA
    assert result.current is None and result.proposed is None
    assert result.not_judged_buckets is not None
    assert result.not_judged_buckets >= 20
    assert result.reason is not None
    assert result.reason.code == "noise.preview.not_judged"


async def test_a_few_missing_buckets_are_partial_not_a_smaller_count(
    db_session,
) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    metrics = await _fill(db_session, source, spike_at=20)
    for metric in metrics[50:53]:
        await db_session.delete(metric)
    await db_session.flush()

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.PARTIAL
    assert result.not_judged_buckets == 3
    assert result.current is not None and result.proposed is not None


async def test_a_source_in_maintenance_is_insufficient(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance, maintenance_mode=True)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.INSUFFICIENT_DATA
    assert result.current is None and result.proposed is None
    assert result.reason is not None
    assert result.reason.code == "noise.preview.maintenance"


async def test_a_not_monitored_source_is_insufficient(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance, monitoring_enabled=False)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.INSUFFICIENT_DATA
    assert result.current is None and result.proposed is None
    assert result.reason is not None
    assert result.reason.code == "noise.preview.not_monitored"


async def test_a_too_large_window_refuses_every_count(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance, expected_interval_seconds=30)

    result = await _replay(db_session, source, window_days=28)
    assert result is not None
    assert result.state is ReplayState.TOO_LARGE
    assert result.available is False
    assert result.current is None and result.proposed is None
    assert result.recorded is None
    assert result.not_judged_buckets is None
    assert result.maintenance_buckets is None
    assert result.reason is not None
    assert result.reason.code == "noise.preview.too_large"


async def test_maintenance_suppressed_buckets_warn_without_changing_counts(
    db_session,
) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)
    # 30 of 167 buckets: 18 %, past the 10 % warning line.
    db_session.add(
        LogSourceAnomaly(
            log_source_id=source.id,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            severity=Severity.MEDIUM,
            state=AnomalyState.RESOLVED,
            detected_at=START + timedelta(hours=2),
            resolved_at=START + timedelta(hours=32),
            suppressed=True,
        )
    )
    await db_session.flush()

    result = await _replay(db_session, source)
    assert result is not None
    assert result.state is ReplayState.COMPLETE
    assert result.maintenance_buckets >= 30
    assert [warning.code for warning in result.warnings] == [
        "noise.preview.maintenance_history"
    ]
    assert result.current is not None


# ------------------------------------------------------------------------------ cost
async def test_the_preview_costs_four_statements_at_two_fleet_sizes(
    db_session,
) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)

    # Warm the lazy imports and the statement cache.
    assert (await _replay(db_session, source)) is not None
    with _recorded_statements(db_session) as statements:
        result = await _replay(db_session, source)
    assert result is not None and result.state is ReplayState.COMPLETE
    assert len(statements) == PREVIEW_STATEMENTS, statements

    # Five more sources, each with a full window: the count does not move.
    for index in range(5):
        other = await _source(db_session, instance, qradar_id=10 + index)
        await _reliable_cells(db_session, other)
        await _fill(db_session, other, spike_at=20)
    await db_session.flush()

    with _recorded_statements(db_session) as statements:
        result = await _replay(db_session, source)
    assert result is not None and result.state is ReplayState.COMPLETE
    assert len(statements) == PREVIEW_STATEMENTS, statements


# ---------------------------------------------------------------------------- route
async def _seed_for_api(
    db_session,
    now: datetime | None = None,
    *,
    spike_length: int = 3,
) -> LogSource:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    anchor = now or START
    start = latest_closed_bucket(anchor, BUCKET) - timedelta(hours=BUCKETS - 1)
    await _fill(
        db_session, source, start=start, spike_at=20, spike_length=spike_length
    )
    await db_session.commit()
    return source


def _wire_counts(block: dict[str, Any]) -> dict[str, int | None]:
    return {item["anomaly_type"]: item["incidents"] for item in block["by_detector"]}


def _stable_now() -> datetime:
    """A now that stays in the same hour while the request runs.

    The route reads its own clock, and a grid that rolls over between seeding and
    the request would lose its first bucket (a PARTIAL window instead of COMPLETE).
    """
    now = datetime.now(UTC)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    remaining = (next_hour - now).total_seconds()
    if remaining <= 60:
        time.sleep(remaining + 0.2)
        now = datetime.now(UTC)
    return now.replace(microsecond=0)


class TestRoute:
    def test_a_principal_without_the_read_permission_is_refused(self, db_schema) -> None:
        denied = reader_client(db_schema, principal=NOBODY)
        assert denied.post(
            f"/api/v1/noise/sources/{uuid.uuid4()}/preview", json={}
        ).status_code == 403
        unrelated = reader_client(db_schema, principal=ALERTS_ONLY)
        assert unrelated.post(
            f"/api/v1/noise/sources/{uuid.uuid4()}/preview", json={}
        ).status_code == 403

    def test_an_unknown_source_is_404(self, db_schema) -> None:
        client = reader_client(db_schema)
        response = client.post(f"/api/v1/noise/sources/{uuid.uuid4()}/preview", json={})
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"window_days": 14},
            {"window_days": "28"},
            {"thresholds": {"spike_ratio": 0.4}},
            {"thresholds": {"deviation_z": 21}},
            {"thresholds": {"not_a_knob": 1}},
            {"thresholds": {"direction": "up"}},
        ],
    )
    def test_invalid_bodies_are_422(self, db_schema, body: dict) -> None:
        client = reader_client(db_schema)
        response = client.post(
            f"/api/v1/noise/sources/{uuid.uuid4()}/preview", json=body
        )
        assert response.status_code == 422

    async def test_the_route_applies_the_proposed_knobs(self, db_session, db_schema) -> None:
        source = await _seed_for_api(db_session, _stable_now())
        client = reader_client(db_schema)
        response = client.post(
            f"/api/v1/noise/sources/{source.id}/preview",
            json={"window_days": 7, "thresholds": {"spike_ratio": 10}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        current = {
            item["anomaly_type"]: item["incidents"]
            for item in body["current"]["by_detector"]
        }
        proposed = {
            item["anomaly_type"]: item["incidents"]
            for item in body["proposed"]["by_detector"]
        }
        assert current["VOLUME_SPIKE"] == 1
        assert proposed["VOLUME_SPIKE"] == 0

    async def test_the_route_marks_an_excluded_detector_null_not_zero(
        self, db_session, db_schema
    ) -> None:
        source = await _seed_for_api(db_session, _stable_now())
        client = reader_client(db_schema)
        response = client.post(
            f"/api/v1/noise/sources/{source.id}/preview",
            json={"window_days": 7, "thresholds": {"direction": "spike"}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        proposed = {
            item["anomaly_type"]: item for item in body["proposed"]["by_detector"]
        }
        assert proposed["VOLUME_DROP"] == {
            "anomaly_type": "VOLUME_DROP",
            "incidents": None,
            "excluded_by_direction": True,
        }
        assert body["proposed"]["total_incidents"] == sum(
            item["incidents"]
            for item in body["proposed"]["by_detector"]
            if item["incidents"] is not None
        )
        assert body["proposed"]["total_incidents"] == proposed["VOLUME_SPIKE"][
            "incidents"
        ]

    async def test_the_route_returns_the_wire_shape_and_writes_nothing(
        self, db_session, db_schema
    ) -> None:
        source = await _seed_for_api(db_session, _stable_now())
        client = reader_client(db_schema)
        before = await db_session.scalar(select(func.count()).select_from(AuditLog))
        response = client.post(
            f"/api/v1/noise/sources/{source.id}/preview", json={"window_days": 7}
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "state",
            "available",
            "window",
            "baseline",
            "current",
            "proposed",
            "recorded",
            "not_replayable",
            "not_judged_buckets",
            "maintenance_buckets",
            "warnings",
            "reason",
            "effective",
        }
        assert set(body["effective"]) == {"criticality", "lab_mode"}
        assert body["effective"] == {"criticality": "MEDIUM", "lab_mode": False}
        assert set(body["window"]) == {
            "days",
            "first_bucket",
            "last_bucket",
            "covered_days",
        }
        assert set(body["baseline"]) == {
            "computed_at",
            "baseline_version",
            "lookback_days",
        }
        assert set(body["current"]) == {"total_incidents", "by_detector"}
        assert set(body["current"]["by_detector"][0]) == {
            "anomaly_type",
            "incidents",
            "excluded_by_direction",
        }
        assert set(body["recorded"]) == {"incidents", "opened", "suppressed"}
        assert body["state"] == "COMPLETE"
        assert body["available"] is True
        after = await db_session.scalar(select(func.count()).select_from(AuditLog))
        assert after == before == 0


# Canonical JSON of preview_out() on the existing spike-and-drop fixture, captured
# before a preview body could carry criticality. `effective` is the one field added
# since; an omitted criticality must leave every other byte as it was.
_TODAY_OMITTED = (
    '{"available":true,"baseline":{"baseline_version":3,"computed_at":"2026-08-03T00:00:'
    '00Z","lookback_days":28},"current":{"by_detector":[{"anomaly_type":"NO_EVENTS","exclu'
    'ded_by_direction":false,"incidents":0},{"anomaly_type":"VOLUME_DROP","excluded_by_dir'
    'ection":false,"incidents":1},{"anomaly_type":"VOLUME_SPIKE","excluded_by_direction":f'
    'alse,"incidents":1}],"total_incidents":2},"maintenance_buckets":0,"not_judged_buckets'
    '":0,"not_replayable":[{"anomaly_type":"PARSING_DEGRADATION","reason":{"code":"noise.p'
    'review.not_replayable","params":{"reason":"bucket did not measure PARSE_RATIOS"},"te'
    'xt":"Not replayed: bucket did not measure PARSE_RATIOS"}},{"anomaly_type":"UNKNOWN_E'
    'VENT_SPIKE","reason":{"code":"noise.preview.not_replayable","params":{"reason":"bucke'
    't did not measure UNKNOWN_EVENT_COUNT"},"text":"Not replayed: bucket did not measure '
    'UNKNOWN_EVENT_COUNT"}},{"anomaly_type":"TIMESTAMP_DELAY","reason":{"code":"noise.prev'
    'iew.not_replayable","params":{"reason":"bucket did not measure INGESTION_DELAY"},"tex'
    't":"Not replayed: bucket did not measure INGESTION_DELAY"}},{"anomaly_type":"CARDINAL'
    'ITY_DROP","reason":{"code":"noise.preview.not_replayable","params":{"reason":"no dist'
    'inct_source_ip_count baseline is built"},"text":"Not replayed: no distinct_source_ip_'
    'count baseline is built"}},{"anomaly_type":"COLLECTION_ERROR","reason":{"code":"noise'
    '.preview.not_replayable","params":{"reason":"bucket did not measure COLLECTION_ERRORS'
    '"},"text":"Not replayed: bucket did not measure COLLECTION_ERRORS"}},{"anomaly_type":'
    '"REPEATED_PAYLOAD","reason":{"code":"noise.preview.not_replayable","params":{"reason"'
    ':"bucket did not measure PAYLOAD_SIGNATURE"},"text":"Not replayed: bucket did not mea'
    'sure PAYLOAD_SIGNATURE"}}],"proposed":{"by_detector":[{"anomaly_type":"NO_EVENTS","ex'
    'cluded_by_direction":false,"incidents":0},{"anomaly_type":"VOLUME_DROP","excluded_by_'
    'direction":false,"incidents":1},{"anomaly_type":"VOLUME_SPIKE","excluded_by_direction'
    '":false,"incidents":1}],"total_incidents":2},"reason":null,"recorded":{"incidents":0,'
    '"opened":0,"suppressed":0},"state":"COMPLETE","warnings":[],"window":{"covered_days":'
    '7,"days":7,"first_bucket":"2026-08-03T01:00:00Z","last_bucket":"2026-08-09T23:00:00Z"'
    "}}"
)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def test_omitted_criticality_matches_todays_response_on_the_existing_fixture(
    db_session,
) -> None:
    """No criticality: the existing spike-and-drop fixture's response is unchanged."""
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20, drop_at=100)

    result = await _replay(db_session, source)
    assert result is not None
    payload = preview_out(result).model_dump(mode="json")
    effective = payload.pop("effective")
    assert effective == {"criticality": "MEDIUM", "lab_mode": False}
    assert _canonical(payload) == _TODAY_OMITTED


async def test_omitted_explicit_and_null_criticality_are_byte_identical(
    db_session, db_schema
) -> None:
    """The route's existing fixture: omitting the field is the stored value."""
    source = await _seed_for_api(db_session, _stable_now())
    client = reader_client(db_schema)
    url = f"/api/v1/noise/sources/{source.id}/preview"
    omitted = client.post(url, json={"window_days": 7})
    explicit = client.post(url, json={"window_days": 7, "criticality": "MEDIUM"})
    blank = client.post(url, json={"window_days": 7, "criticality": None})
    assert omitted.status_code == 200, omitted.text
    assert omitted.content == explicit.content == blank.content
    body = omitted.json()
    assert body["effective"] == {"criticality": "MEDIUM", "lab_mode": False}
    assert _wire_counts(body["current"])["VOLUME_SPIKE"] == 1


async def test_proposed_criticality_reaches_both_resolver_calls(db_session) -> None:
    """LOW moves open_after and resolve_after on the top-level set and per type."""
    instance = await _instance(db_session)
    source = await _source(db_session, instance, criticality=Criticality.MEDIUM)
    settings = _settings()
    resolver = ThresholdResolver(settings)
    default = OutagePolicyMode(settings.outage_alert_default)

    top, by_type = _thresholds(
        resolver, source, {}, [], None, default, criticality=Criticality.LOW
    )
    assert top.open_after == 3
    assert top.resolve_after == 4
    assert by_type[AnomalyType.VOLUME_SPIKE].open_after == 3
    assert by_type[AnomalyType.VOLUME_SPIKE].resolve_after == 4
    assert by_type[AnomalyType.NO_EVENTS].open_after == 3

    stored_top, stored_by = _thresholds(resolver, source, {}, [], None, default)
    assert stored_top.open_after == 2
    assert stored_top.resolve_after == 3
    assert stored_by[AnomalyType.VOLUME_SPIKE].open_after == 2

    lab = ThresholdResolver(_settings(lab_mode=True))
    lab_top, lab_by = _thresholds(
        lab, source, {}, [], None, default, criticality=Criticality.LOW
    )
    stored_lab, _stored_lab_by = _thresholds(lab, source, {}, [], None, default)
    assert lab_top.open_after == stored_lab.open_after == 1
    assert lab_top.resolve_after == stored_lab.resolve_after == 2
    assert lab_by[AnomalyType.VOLUME_SPIKE].open_after == 1
    await db_session.refresh(source)
    assert source.criticality == Criticality.MEDIUM


async def test_a_proposed_criticality_matches_storing_it(
    db_session, db_schema
) -> None:
    """One anomalous bucket opens for CRITICAL (open_after 1) and not for MEDIUM.

    The proposed counts must equal a second preview of the same fixture after the
    column itself is CRITICAL. Dropping the proposed value makes that equality fail.
    """
    source = await _seed_for_api(db_session, _stable_now(), spike_length=1)
    await db_session.refresh(source)
    assert source.criticality == Criticality.MEDIUM
    stored_thresholds = dict(source.custom_thresholds or {})
    audits = await db_session.scalar(select(func.count()).select_from(AuditLog))
    anomalies = await db_session.scalar(
        select(func.count()).select_from(LogSourceAnomaly)
    )

    client = reader_client(db_schema)
    url = f"/api/v1/noise/sources/{source.id}/preview"
    response = client.post(url, json={"window_days": 7, "criticality": "CRITICAL"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"] == {"criticality": "CRITICAL", "lab_mode": False}
    assert _wire_counts(body["current"])["VOLUME_SPIKE"] == 0
    assert _wire_counts(body["proposed"])["VOLUME_SPIKE"] == 1
    assert body["current"]["total_incidents"] == 0
    assert body["proposed"]["total_incidents"] == 1

    await db_session.refresh(source)
    assert source.criticality == Criticality.MEDIUM
    assert dict(source.custom_thresholds or {}) == stored_thresholds
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == audits
    assert (
        await db_session.scalar(select(func.count()).select_from(LogSourceAnomaly))
        == anomalies
    )

    source.criticality = Criticality.CRITICAL
    await db_session.commit()
    stored = client.post(url, json={"window_days": 7})
    assert stored.status_code == 200, stored.text
    stored_body = stored.json()
    assert stored_body["effective"]["criticality"] == "CRITICAL"
    assert stored_body["current"]["by_detector"] == body["proposed"]["by_detector"]
    assert stored_body["current"]["total_incidents"] == body["proposed"]["total_incidents"]
    assert stored_body["proposed"]["by_detector"] == body["proposed"]["by_detector"]


async def test_lab_mode_ignores_a_proposed_criticality_and_says_so(
    db_session, db_schema
) -> None:
    """Two anomalous buckets: LOW would not open, MEDIUM would, LAB_MODE opens both.

    A proposed LOW must count the same as the stored MEDIUM, and the same as LOW
    actually stored, with the existing lab_mode flag set.
    """
    source = await _seed_for_api(db_session, _stable_now(), spike_length=2)
    client = reader_client(
        db_schema, settings=Settings(_env_file=None, debug=False, lab_mode=True)
    )
    url = f"/api/v1/noise/sources/{source.id}/preview"
    response = client.post(url, json={"window_days": 7, "criticality": "LOW"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["effective"] == {"criticality": "LOW", "lab_mode": True}
    assert _wire_counts(body["current"]) == _wire_counts(body["proposed"])
    assert _wire_counts(body["proposed"])["VOLUME_SPIKE"] == 1

    source.criticality = Criticality.LOW
    await db_session.commit()
    stored = client.post(url, json={"window_days": 7})
    assert stored.status_code == 200, stored.text
    stored_body = stored.json()
    assert stored_body["effective"] == {"criticality": "LOW", "lab_mode": True}
    assert stored_body["current"]["by_detector"] == body["proposed"]["by_detector"]
    assert stored_body["current"]["total_incidents"] == body["proposed"]["total_incidents"]


async def test_unknown_criticality_is_422_and_writes_nothing(
    db_session, db_schema
) -> None:
    source = await _seed_for_api(db_session, _stable_now())
    await db_session.refresh(source)
    criticality = source.criticality
    thresholds = dict(source.custom_thresholds or {})
    audits = await db_session.scalar(select(func.count()).select_from(AuditLog))
    anomalies = await db_session.scalar(
        select(func.count()).select_from(LogSourceAnomaly)
    )
    client = reader_client(db_schema)
    url = f"/api/v1/noise/sources/{source.id}/preview"

    for value in ("URGENT", "critical", 1, ""):
        response = client.post(url, json={"window_days": 7, "criticality": value})
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail
        assert detail[0]["loc"] == ["body", "criticality"]
        assert "state" not in response.json()

    missing = client.post(
        f"/api/v1/noise/sources/{uuid.uuid4()}/preview",
        json={"criticality": "URGENT"},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"][0]["loc"] == ["body", "criticality"]

    await db_session.refresh(source)
    assert source.criticality == criticality
    assert dict(source.custom_thresholds or {}) == thresholds
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == audits
    assert (
        await db_session.scalar(select(func.count()).select_from(LogSourceAnomaly))
        == anomalies
    )


async def test_a_refused_window_still_names_the_proposed_criticality(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, count=5 * 24)

    result = await _replay(
        db_session,
        source,
        window_days=7,
        now=START + timedelta(days=5),
        criticality=Criticality.LOW,
    )
    assert result is not None
    assert result.state is ReplayState.INSUFFICIENT_DATA
    assert result.current is None and result.proposed is None
    assert result.effective_criticality is Criticality.LOW
    assert result.lab_mode is False


async def test_a_proposed_criticality_still_costs_four_statements(db_session) -> None:
    instance = await _instance(db_session)
    source = await _source(db_session, instance)
    await _reliable_cells(db_session, source)
    await _fill(db_session, source, spike_at=20)
    assert await _replay(db_session, source, criticality=Criticality.LOW) is not None

    with _recorded_statements(db_session) as statements:
        result = await _replay(db_session, source, criticality=Criticality.CRITICAL)
    assert result is not None and result.state is ReplayState.COMPLETE
    assert len(statements) == PREVIEW_STATEMENTS, statements
