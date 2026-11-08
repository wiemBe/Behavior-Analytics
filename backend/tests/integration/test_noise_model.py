"""The shadow count-model route (SOC-17e).

The loader's four statements stay four at two history sizes. A disabled
preview is a 404 before any SELECT. The route writes no row and opens no
alert. One 28-day fixture carries an OK count cell, an unfit cell and a
level-shift proposal, and only the NOISE-dispositioned spike buckets count.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.custom_rules.preview import bucket_grid
from app.main import create_app
from app.models import Base
from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Severity,
    TriageDisposition,
    TriageStatus,
)
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceAnomaly, LogSourceBaseline, LogSourceMetric
from app.models.triage import AnomalyTriage
from app.noise.models import LowVolumeModel, ModelState
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)
POISSON = [0, 1, 2, 2, 3, 3, 4, 4, 5, 6]
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())
ALERTS_ONLY = Principal(subject="alerts", permissions=frozenset({"alert:ack"}))
MODEL_STATEMENTS = 4


@contextmanager
def _recorded_statements(bind: Any):
    statements: list[str] = []

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = bind.sync_engine if hasattr(bind, "sync_engine") else bind.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _selects(statements: list[str]) -> list[str]:
    return [item for item in statements if item.lstrip().lower().startswith("select")]


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


@contextmanager
def _client(db_url: str, **kwargs: Any):
    client = reader_client(db_url, **kwargs)
    try:
        yield client
    finally:
        client.engine.sync_engine.dispose()  # type: ignore[attr-defined]


async def _instance(session: Any) -> QRadarInstance:
    instance = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(instance)
    await session.flush()
    return instance


async def _source(session: Any, instance: QRadarInstance, **kwargs: Any) -> LogSource:
    source = LogSource(
        instance_id=instance.id,
        qradar_id=int(uuid.uuid4().int % 1_000_000),
        name=f"src-{uuid.uuid4().hex[:8]}",
        expected_interval_seconds=kwargs.pop("expected_interval_seconds", 3600),
        **kwargs,
    )
    session.add(source)
    await session.flush()
    return source


def _baseline(
    source: LogSource,
    *,
    metric: str,
    weekday: int,
    hour: int,
    observations: list[float],
    median: float,
    p05: float,
    p95: float,
) -> LogSourceBaseline:
    return LogSourceBaseline(
        log_source_id=source.id,
        metric_name=metric,
        weekday=weekday,
        hour=hour,
        median=median,
        mad=1.0,
        p05=p05,
        p95=p95,
        sample_count=len(observations),
        is_reliable=True,
        completeness=1.0,
        baseline_version=1,
        observations=observations,
        excluded_sample_count=0,
        exclusion_counts={},
        computed_at=NOW,
    )


async def _all_cells(
    session: Any,
    source: LogSource,
    *,
    ok_key: tuple[int, int] | None = None,
) -> None:
    rows: list[LogSourceBaseline] = []
    for weekday in range(1, 8):
        for hour in range(24):
            if ok_key == (weekday, hour):
                count_obs: list[float] = [float(item) for item in POISSON]
                count_median, count_p05, count_p95 = 3.0, 0.0, 6.0
            else:
                count_obs = [3.0] * 8
                count_median, count_p05, count_p95 = 3.0, 2.0, 4.0
            rows.append(
                _baseline(
                    source, metric="event_count", weekday=weekday, hour=hour,
                    observations=count_obs, median=count_median, p05=count_p05, p95=count_p95,
                )
            )
            rows.append(
                _baseline(
                    source, metric="average_eps", weekday=weekday, hour=hour,
                    observations=[50.5, 49.5] * 4, median=50.0, p05=40.0, p95=60.0,
                )
            )
    session.add_all(rows)
    await session.flush()


def _metric_row(
    source_id: uuid.UUID, start: datetime, *, eps: float, events: int, width: int
) -> dict[str, Any]:
    return {
        "log_source_id": source_id,
        "bucket_start": start,
        "bucket_seconds": width,
        "event_count": events,
        "average_eps": eps,
        "peak_eps": eps,
        "unknown_event_count": 0,
        "stored_event_count": events,
        "distinct_qid_count": 5,
        "distinct_username_count": 5,
        "distinct_source_ip_count": 5,
        "collection_error_count": 0,
        "completeness": BucketCompleteness.COMPLETE.value,
        "query_provenance": {},
        "payload_signature": "calm",
    }


async def _insert_metrics(session: Any, rows: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), 2000):
        await session.execute(insert(LogSourceMetric), rows[offset:offset + 2000])
    await session.flush()


async def _counts(session: Any) -> dict[str, int]:
    found: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        found[table.name] = int(
            await session.scalar(select(func.count()).select_from(table)) or 0
        )
    return found


async def _incident(
    session: Any,
    source: LogSource,
    *,
    start: datetime,
    end: datetime,
    disposition: TriageDisposition | None,
) -> None:
    incident = LogSourceAnomaly(
        log_source_id=source.id,
        anomaly_type=AnomalyType.VOLUME_SPIKE,
        severity=Severity.LOW,
        state=AnomalyState.RESOLVED,
        detected_at=start,
        resolved_at=end,
        anomaly_start=start,
        anomaly_end=end,
        details={},
        suppressed=False,
    )
    session.add(incident)
    await session.flush()
    if disposition is None:
        return
    session.add(
        AnomalyTriage(
            family=AnomalyFamily.LOG_SOURCE,
            anomaly_id=incident.id,
            status=TriageStatus.CLOSED,
            disposition=disposition,
            updated_by="tester",
            version=1,
        )
    )
    await session.flush()


def _stable_now() -> datetime:
    """Stay inside one 300 s bucket for the request that follows.

    The route reads ``datetime.now`` and cannot take a clock. Waiting out the
    last two minutes of the bucket keeps the closed bucket from rolling while
    the fixture is inserted and the request runs.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    remaining = 300 - (int(now.timestamp()) % 300)
    if remaining < 120:
        time.sleep(remaining + 1)
        now = datetime.now(UTC).replace(microsecond=0)
    return now


class TestLoader:
    async def test_statement_count_is_the_same_at_two_history_sizes(self, db_session) -> None:
        instance = await _instance(db_session)
        grid = bucket_grid(NOW, 28 * 24, 3600)
        counts: list[int] = []
        for size in (5, 80):
            source = await _source(db_session, instance, expected_interval_seconds=3600)
            await _insert_metrics(
                db_session,
                [
                    _metric_row(source.id, start, eps=50.0, events=15000, width=3600)
                    for start in grid[:size]
                ],
            )
            with _recorded_statements(db_session) as statements:
                view = await LowVolumeModel(db_session, now=NOW).load(source.id)
            assert view is not None
            counts.append(len(_selects(statements)))
        assert counts[0] == counts[1] == MODEL_STATEMENTS

    async def test_unknown_source_reads_once(self, db_session) -> None:
        with _recorded_statements(db_session) as statements:
            view = await LowVolumeModel(db_session, now=NOW).load(uuid.uuid4())
        assert view is None
        assert len(_selects(statements)) == 1

    async def test_too_large_window_stops_after_the_source(self, db_session) -> None:
        instance = await _instance(db_session)
        source = await _source(db_session, instance, expected_interval_seconds=1)
        with _recorded_statements(db_session) as statements:
            view = await LowVolumeModel(db_session, now=NOW).load(source.id)
        assert view is not None
        assert len(_selects(statements)) == 1
        assert view.state is ModelState.INSUFFICIENT_DATA
        assert view.reason is not None
        assert view.reason.code == "noise.preview.too_large"
        assert view.cells == ()
        assert all(row.replayed_flags is None for row in view.calibration)

    async def test_short_coverage_withholds_tails(self, db_session) -> None:
        instance = await _instance(db_session)
        source = await _source(db_session, instance, expected_interval_seconds=3600)
        grid = bucket_grid(NOW, 28 * 24, 3600)
        await _all_cells(db_session, source, ok_key=(grid[-1].isoweekday(), grid[-1].hour))
        await _insert_metrics(
            db_session,
            [
                _metric_row(source.id, start, eps=50.0, events=3, width=3600)
                for start in grid[-48:]
            ],
        )
        view = await LowVolumeModel(db_session, now=NOW).load(source.id)
        assert view is not None
        assert view.covered_days < 7
        assert view.state is ModelState.INSUFFICIENT_DATA
        assert view.reason is not None
        assert view.reason.code == "noise.model.insufficient_coverage"
        fitted = next(
            cell for cell in view.cells
            if cell.metric == "event_count"
            and (cell.weekday, cell.hour) == (grid[-1].isoweekday(), grid[-1].hour)
        )
        assert fitted.model == "poisson"
        assert fitted.upper_tail_below is None and fitted.lower_tail_below is None
        assert view.calibration_reason is not None
        assert view.calibration_reason.code == "noise.preview.insufficient_coverage"
        assert all(row.replayed_flags is None for row in view.calibration)

    async def test_partial_window_scores_present_buckets(self, db_session) -> None:
        instance = await _instance(db_session)
        source = await _source(db_session, instance, expected_interval_seconds=3600)
        grid = bucket_grid(NOW, 28 * 24, 3600)
        key = (grid[-1].isoweekday(), grid[-1].hour)
        await _all_cells(db_session, source, ok_key=key)
        await _insert_metrics(
            db_session,
            [
                _metric_row(source.id, start, eps=50.0, events=3, width=3600)
                for start in grid[-240:]
            ],
        )
        view = await LowVolumeModel(db_session, now=NOW).load(source.id)
        assert view is not None
        assert 7 <= view.covered_days < 28
        assert view.state is ModelState.PARTIAL
        assert view.reason is not None and view.reason.code == "noise.model.partial"
        fitted = next(
            cell for cell in view.cells
            if cell.metric == "event_count" and (cell.weekday, cell.hour) == key
        )
        assert fitted.state is ModelState.PARTIAL
        assert fitted.upper_tail_below is not None
        assert fitted.lower_tail_below is not None

    async def test_not_monitored_is_not_judged(self, db_session) -> None:
        instance = await _instance(db_session)
        source = await _source(
            db_session, instance, monitoring_enabled=False, expected_interval_seconds=3600
        )
        await _all_cells(db_session, source, ok_key=(1, 10))
        view = await LowVolumeModel(db_session, now=NOW).load(source.id)
        assert view is not None
        assert view.state is ModelState.NOT_JUDGED
        assert view.reason is not None and view.reason.code == "noise.model.not_monitored"
        assert view.cells
        assert all(cell.state is ModelState.NOT_JUDGED for cell in view.cells)
        assert all(cell.upper_tail_below is None for cell in view.cells)
        assert all(cell.level_shift is None for cell in view.cells)
        assert view.cells[0].shrinkage.raw_median is not None
        assert view.calibration_reason is not None
        assert view.calibration_reason.code == "noise.preview.not_monitored"

    async def test_maintenance_refuses_calibration_only(self, db_session) -> None:
        instance = await _instance(db_session)
        source = await _source(
            db_session, instance, maintenance_mode=True, expected_interval_seconds=3600
        )
        await _all_cells(db_session, source, ok_key=(1, 10))
        view = await LowVolumeModel(db_session, now=NOW).load(source.id)
        assert view is not None
        fitted = next(cell for cell in view.cells if cell.model == "poisson")
        assert fitted.mean is not None
        assert fitted.upper_tail_below is None
        assert view.calibration_reason is not None
        assert view.calibration_reason.code == "noise.preview.maintenance"
        assert view.state is ModelState.INSUFFICIENT_DATA
        assert view.reason is not None
        assert view.reason.code == "noise.model.insufficient_coverage"


class TestRoute:
    def test_a_principal_without_the_read_permission_is_refused(self, db_schema: str) -> None:
        with _client(db_schema, principal=NOBODY) as denied:
            assert denied.get(f"/api/v1/noise/sources/{uuid.uuid4()}/model").status_code == 403
        with _client(db_schema, principal=ALERTS_ONLY) as unrelated:
            response = unrelated.get(f"/api/v1/noise/sources/{uuid.uuid4()}/model")
        assert response.status_code == 403

    def test_an_unknown_source_is_404(self, db_schema: str) -> None:
        with (
            _client(db_schema) as client,
            _recorded_statements(client.engine) as statements,  # type: ignore[attr-defined]
        ):
            response = client.get(f"/api/v1/noise/sources/{uuid.uuid4()}/model")
        assert response.status_code == 404
        assert response.json()["detail"] == "log source not found"
        assert len(_selects(statements)) == 1

    def test_disabled_setting_is_404_and_issues_no_select(self, db_schema: str) -> None:
        settings = get_settings().model_copy(update={"low_volume_model_preview_enabled": False})
        with (
            _client(db_schema, settings=settings) as client,
            _recorded_statements(client.engine) as statements,  # type: ignore[attr-defined]
        ):
            response = client.get(f"/api/v1/noise/sources/{uuid.uuid4()}/model")
        assert response.status_code == 404
        assert response.json()["detail"] == "low-volume model preview is not enabled"
        assert _selects(statements) == []

    async def test_statement_count_on_the_route_matches_two_sizes(
        self, db_session: Any, db_schema: str
    ) -> None:
        instance = await _instance(db_session)
        now = datetime.now(UTC).replace(microsecond=0)
        grid = bucket_grid(now, 28 * 24, 3600)
        sources = []
        for size in (5, 80):
            source = await _source(db_session, instance, expected_interval_seconds=3600)
            await _insert_metrics(
                db_session,
                [
                    _metric_row(source.id, start, eps=50.0, events=15000, width=3600)
                    for start in grid[:size]
                ],
            )
            sources.append(source)
        await db_session.commit()
        seen: list[int] = []
        with _client(db_schema) as client:
            for source in sources:
                with _recorded_statements(client.engine) as statements:  # type: ignore[attr-defined]
                    response = client.get(f"/api/v1/noise/sources/{source.id}/model")
                assert response.status_code == 200, response.text
                seen.append(len(_selects(statements)))
        assert seen[0] == seen[1] == MODEL_STATEMENTS

    async def test_the_route_writes_nothing_and_opens_no_alert(
        self, db_session: Any, db_schema: str
    ) -> None:
        instance = await _instance(db_session)
        source = await _source(db_session, instance, expected_interval_seconds=3600)
        await db_session.commit()
        before = await _counts(db_session)
        with _client(db_schema) as client:
            response = client.get(f"/api/v1/noise/sources/{source.id}/model")
        assert response.status_code == 200
        after = await _counts(db_session)
        assert after == before
        assert after["audit_log"] == before["audit_log"]
        assert after["alert"] == before["alert"]

    async def test_ok_cell_level_shift_and_noise_calibration(
        self, db_session: Any, db_schema: str
    ) -> None:
        now = _stable_now()
        grid = bucket_grid(now, 28 * 24, 300)
        recent_after = grid[-1] + timedelta(seconds=300) - timedelta(days=7)
        recent_counts: Counter[tuple[int, int]] = Counter()
        for start in grid:
            if start >= recent_after:
                recent_counts[(start.isoweekday(), start.hour)] += 1
        ok_key = max(recent_counts, key=recent_counts.get)
        assert recent_counts[ok_key] >= 8
        spikes: list[datetime] | None = None
        for index in range(len(grid) - 6):
            window = grid[index:index + 6]
            if all((item.isoweekday(), item.hour) != ok_key for item in window):
                spikes = window
                break
        assert spikes is not None
        ok_starts = {
            start for start in grid
            if (start.isoweekday(), start.hour) == ok_key and start >= recent_after
        }
        spike_set = set(spikes)
        instance = await _instance(db_session)
        source = await _source(db_session, instance, expected_interval_seconds=300)
        await _all_cells(db_session, source, ok_key=ok_key)
        rows = []
        for start in grid:
            if start in spike_set:
                eps, events = 200.0, 60000
            elif start in ok_starts:
                eps, events = 50.0, 30
            else:
                eps, events = 50.0, 3
            rows.append(_metric_row(source.id, start, eps=eps, events=events, width=300))
        await _insert_metrics(db_session, rows)
        await _incident(
            db_session, source, start=spikes[0], end=spikes[1],
            disposition=TriageDisposition.NOISE,
        )
        await _incident(
            db_session, source, start=spikes[0], end=spikes[0],
            disposition=TriageDisposition.NOISE,
        )
        await _incident(
            db_session, source, start=spikes[2], end=spikes[3],
            disposition=TriageDisposition.TRUE_POSITIVE,
        )
        await _incident(db_session, source, start=spikes[4], end=spikes[5], disposition=None)
        await db_session.commit()
        before = await _counts(db_session)
        with _client(db_schema) as client:
            response = client.get(f"/api/v1/noise/sources/{source.id}/model")
        assert response.status_code == 200, response.text
        after = await _counts(db_session)
        assert after == before
        assert after["alert"] == before["alert"]
        assert after["audit_log"] == before["audit_log"]
        body = response.json()
        assert body["state"] == "OK"
        assert body["window_days"] == 28
        assert body["covered_days"] == 28
        assert body["p_threshold"] == 0.001
        assert body["shrinkage_k"] == 8
        assert len(body["cells"]) == 7 * 24 * 2
        ok_cells = [
            cell for cell in body["cells"]
            if cell["metric"] == "event_count"
            and cell["weekday"] == ok_key[0]
            and cell["hour"] == ok_key[1]
        ]
        assert len(ok_cells) == 1
        ok = ok_cells[0]
        assert ok["state"] == "OK"
        assert ok["model"] == "poisson"
        assert ok["upper_tail_below"] == len(ok_starts)
        assert ok["lower_tail_below"] == 0
        assert ok["level_shift"] is not None
        assert ok["level_shift"]["recent_median"] == 30.0
        assert ok["level_shift"]["cell_median"] == 3.0
        started = datetime.fromisoformat(ok["level_shift"]["started_at"])
        assert started == min(ok_starts)
        unfit = next(
            cell for cell in body["cells"]
            if cell["state"] == "INSUFFICIENT_DATA"
            and cell["reason"]["code"] == "noise.model.not_counts"
        )
        assert unfit["upper_tail_below"] is None
        assert unfit["model"] is None
        spike = next(
            row for row in body["calibration"] if row["anomaly_type"] == "VOLUME_SPIKE"
        )
        assert spike["replayed_flags"] == 6
        assert spike["noise_flags"] == 2
        assert spike["reason"] is None
        drop = next(
            row for row in body["calibration"] if row["anomaly_type"] == "VOLUME_DROP"
        )
        assert drop["replayed_flags"] == 0 and drop["noise_flags"] == 0
        parsing = next(
            row for row in body["calibration"] if row["anomaly_type"] == "PARSING_DEGRADATION"
        )
        assert parsing["replayed_flags"] is None and parsing["noise_flags"] is None
        assert parsing["reason"]["code"] == "noise.preview.not_replayable"
