"""Stored-query projection for the JDBC list/detail (design §4, §6): SQL only.

One SELECT yields each JDBC-like source with its safe config (LEFT JOIN) and the exact
target bucket (LEFT JOIN on the composite key, one probe per source, never a history scan).
The flow CASE mirrors `flow.flow_state`: only a COMPLETE live target row gives an exact
count; PARTIAL (or `fleet_empty`) is PARTIAL; a known truncated attempt is TRUNCATED only
when the target row is absent; all else is NOT_COLLECTED, null count. Filters run pre-page.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, case, false, func, not_, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import aliased

from app.collectors.log_source_collector import COLLECTOR_NAME, INVENTORY_RUN_KEY
from app.history.origin import PHASE_A_HISTORY_SOURCES, is_live_clause
from app.jdbc.flow import AttemptState, LastObservation, target_bucket
from app.jdbc.parameters import UNVERIFIED
from app.log_source_policy import not_absent
from app.models.enums import BucketCompleteness
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig, JdbcSyncState
from app.models.log_source import LogSource, LogSourceMetric
from app.models.log_source_pause import REASON_ABSENT, LogSourcePause
from app.models.monitoring import CollectionWatermark

JDBC_PROTOCOL_ID = 8
JDBC_LIKE_PROTOCOLS: tuple[int, ...] = (JDBC_PROTOCOL_ID, *sorted(UNVERIFIED))
#: The last-observation read never looks further back than this (or the stale window).
LAST_OBSERVATION_LOOKBACK = timedelta(hours=24)
_CONFIG_COLUMNS = tuple(
    column.name for column in JdbcSourceConfig.__table__.columns if column.name != "log_source_id"
)


@dataclass(frozen=True)
class TargetWindow:
    """The expected bucket [start, end), computed once from `flow.target_bucket`."""

    start: datetime
    end: datetime
    #: A truncated attempt is known. It only ever marks a source whose target row is absent.
    truncated_attempt: bool = False

    @property
    def width_seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


def target_window(
    now: datetime, *, cadence_seconds: int, width_seconds: int, anchor: int,
    truncated_attempt: bool = False,
) -> TargetWindow:
    start, end = target_bucket(
        now, cadence_seconds=cadence_seconds, width_seconds=width_seconds, anchor=anchor
    )
    return TargetWindow(start, end, truncated_attempt)


@dataclass(frozen=True)
class JdbcFilters:
    q: str | None = None
    enabled: bool | None = None
    protocol: int | None = None
    instance_id: uuid.UUID | None = None
    flow_state: str | None = None


def absent_since_column() -> ColumnElement[datetime]:
    """The open ABSENT span's start, correlated to the unaliased LogSource.

    The partial unique index allows one open span per reason, so this is a scalar. It
    accompanies `not_absent()` in the same statement; it never adds a query per source.
    """
    return select(LogSourcePause.starts_at).where(
        LogSourcePause.log_source_id == LogSource.id,
        LogSourcePause.reason == REASON_ABSENT,
        LogSourcePause.ends_at.is_(None),
    ).correlate(LogSource).scalar_subquery()


def _flow_columns(
    tm: Any, window: TargetWindow, *, absent_aware: bool = False,
) -> dict[str, ColumnElement[Any]]:
    """The §4 table as CASE expressions over the aliased target-row columns `tm`.

    With `absent_aware`, the shared `not_absent()` predicate guards the legacy CASE: an
    open ABSENT span yields its own state, NOT_COLLECTED and null figures whatever the
    stored flag or the target row says. Off, the expressions are unchanged.
    """
    enabled = LogSource.enabled.is_(True)
    present = tm.log_source_id.is_not(None)
    live = and_(present, is_live_clause(tm))
    partial = and_(live, or_(
        tm.completeness == BucketCompleteness.PARTIAL.value,
        func.coalesce(tm.query_provenance.op("->>")("fleet_empty") == "true", false()),
    ))
    truncated = not_(present) if window.truncated_attempt else false()
    exact = and_(
        enabled, live, not_(partial), tm.completeness == BucketCompleteness.COMPLETE.value,
        tm.event_count.is_not(None), tm.event_count >= 0,
    )
    measurement = case(
        (not_(enabled), "NOT_COLLECTED"), (partial, "PARTIAL"), (truncated, "TRUNCATED"),
        (exact, "EXACT"), else_="NOT_COLLECTED",
    )
    state = case(
        (not_(enabled), "DISABLED_IN_QRADAR"),
        (and_(exact, tm.event_count > 0), "RECEIVING"),
        (exact, "NO_ATTRIBUTED_EVENTS"), else_="NOT_MEASURED",
    )
    # Only an exact bucket carries a count or a rate; anything else is null, never zero.
    columns: dict[str, ColumnElement[Any]] = {
        "flow_state": state, "measurement": measurement,
        "event_count": case((exact, tm.event_count)),
        "average_eps": case((exact, tm.average_eps)),
    }
    if not absent_aware:
        return columns
    present_now = not_absent()
    return {
        "flow_state": case((present_now, state), else_="ABSENT_FROM_INVENTORY"),
        "measurement": case((present_now, measurement), else_="NOT_COLLECTED"),
        "event_count": case((present_now, columns["event_count"])),
        "average_eps": case((present_now, columns["average_eps"])),
    }


def _conditions(
    filters: JdbcFilters, flow: dict[str, ColumnElement[Any]] | None, *,
    omit_absent: bool = False,
) -> list[Any]:
    conditions: list[Any] = [LogSource.protocol_type_id.in_(JDBC_LIKE_PROTOCOLS)]
    needle = (filters.q or "").strip()
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(LogSource.name.ilike(f"%{escaped}%", escape="\\"))
    if filters.enabled is not None:
        conditions.append(LogSource.enabled.is_(filters.enabled))
    if filters.protocol is not None:
        conditions.append(LogSource.protocol_type_id == filters.protocol)
    if filters.instance_id is not None:
        conditions.append(LogSource.instance_id == filters.instance_id)
    if filters.flow_state is not None and flow is not None:
        conditions.append(flow["flow_state"] == filters.flow_state)
    if omit_absent:
        conditions.append(not_absent())
    return conditions


def _target_join(tm: Any, window: TargetWindow) -> ColumnElement[bool]:
    """The composite key plus the window's width: a row of another width is not the target."""
    return and_(
        tm.log_source_id == LogSource.id, tm.bucket_start == window.start,
        tm.bucket_seconds == window.width_seconds,
    )


def page_query(
    window: TargetWindow, filters: JdbcFilters, *, limit: int, offset: int,
    absent_aware: bool = False, omit_absent: bool = False,
) -> Select[Any]:
    """Statement 2 of 4: one page, filtered before pagination, stable name/id order."""
    tm, cfg = aliased(LogSourceMetric), aliased(JdbcSourceConfig)
    flow = _flow_columns(tm, window, absent_aware=absent_aware)
    columns = [
        LogSource.id, LogSource.instance_id, LogSource.qradar_id, LogSource.name,
        LogSource.protocol_type_id, LogSource.enabled, LogSource.qradar_status,
        LogSource.monitoring_enabled, cfg.log_source_id.is_not(None).label("config_present"),
        *(getattr(cfg, name).label(f"cfg_{name}") for name in _CONFIG_COLUMNS),
        tm.log_source_id.is_not(None).label("target_present"),
        tm.bucket_seconds.label("target_bucket_seconds"),
        tm.collected_at.label("target_collected_at"),
        *(expr.label(name) for name, expr in flow.items()),
    ]
    if absent_aware:
        columns.append(absent_since_column().label("absent_since"))
    return (
        select(*columns)
        .select_from(LogSource)
        .outerjoin(cfg, cfg.log_source_id == LogSource.id)
        .outerjoin(tm, _target_join(tm, window))
        .where(*_conditions(filters, flow, omit_absent=omit_absent))
        .order_by(LogSource.name, LogSource.id)
        .limit(limit)
        .offset(offset)
    )


def count_query(
    window: TargetWindow, filters: JdbcFilters, *, absent_aware: bool = False,
    omit_absent: bool = False,
) -> Select[Any]:
    """Statement 1 of 4: the page's conditions; the target join only for a flow filter."""
    stmt = select(func.count()).select_from(LogSource)
    flow = None
    if filters.flow_state is not None:
        tm = aliased(LogSourceMetric)
        flow = _flow_columns(tm, window, absent_aware=absent_aware)
        stmt = stmt.outerjoin(tm, _target_join(tm, window))
    return stmt.where(*_conditions(filters, flow, omit_absent=omit_absent))


def last_observation_since(
    target_end: datetime, *, cadence_seconds: int, width_seconds: int
) -> datetime:
    """Lower bound of the last-observation read: the stale window, at least 24 hours."""
    stale = timedelta(seconds=2 * cadence_seconds + width_seconds)
    return target_end - max(LAST_OBSERVATION_LOOKBACK, stale)


def last_observation_query(source_ids: Sequence[uuid.UUID], *, since: datetime) -> Select[Any]:
    """Statement 3 of 4: the latest stored bucket of each page source, in a bounded window.

    Reported beside the target and never used to fill it.
    """
    m = LogSourceMetric
    columns: list[Any] = [
        m.log_source_id, m.bucket_start, m.bucket_seconds, m.event_count, m.completeness,
        m.collection_source, m.collected_at,
        (m.query_provenance.op("->>")("fleet_empty") == "true").label("fleet_empty"),
    ]
    return (
        select(*columns)
        .where(m.log_source_id.in_(list(source_ids)), m.bucket_start >= since)
        .ext(distinct_on(m.log_source_id))
        .order_by(m.log_source_id, m.bucket_start.desc())
    )


def to_last_observation(row: Any) -> LastObservation:
    """A count only for a COMPLETE, non-fleet-empty bucket; a rate or lower bound is None."""
    try:
        completeness = BucketCompleteness(row.completeness)
    except ValueError:
        completeness = BucketCompleteness.MISSING
    end = row.bucket_start + timedelta(seconds=row.bucket_seconds)
    exact = completeness is BucketCompleteness.COMPLETE and not row.fleet_empty
    return LastObservation(
        start=row.bucket_start, end=end, observed_at=row.collected_at or end,
        count=int(row.event_count) if exact and row.event_count >= 0 else None,
        completeness=completeness,
        origin="history" if row.collection_source in PHASE_A_HISTORY_SOURCES else "live",
    )


def run_state_query(instance_id: uuid.UUID | None = None) -> Select[Any]:
    """Statement 4 of 4: per console, the inventory pass and the JDBC sync row, in one read."""
    stmt = (
        select(
            QRadarInstance.id.label("instance_id"),
            CollectionWatermark.watermark_at.label("inventory_last_complete_at"),
            CollectionWatermark.last_run_at.label("inventory_last_attempt_at"),
            CollectionWatermark.collection_metadata[INVENTORY_RUN_KEY].label("inventory_pass"),
            JdbcSyncState.state.label("sync_state"),
            JdbcSyncState.queued_at, JdbcSyncState.started_at, JdbcSyncState.finished_at,
            JdbcSyncState.last_complete_at.label("sync_last_complete_at"),
            JdbcSyncState.failure_code.label("sync_failure_code"),
            JdbcSyncState.failed_page.label("sync_failed_page"),
        )
        .select_from(QRadarInstance)
        .outerjoin(CollectionWatermark, and_(
            CollectionWatermark.instance_id == QRadarInstance.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        ))
        .outerjoin(JdbcSyncState, JdbcSyncState.instance_id == QRadarInstance.id)
        .order_by(QRadarInstance.id)
    )
    return stmt if instance_id is None else stmt.where(QRadarInstance.id == instance_id)


def inventory_attempt_state(inventory_pass: Any) -> AttemptState:
    """The last finished inventory pass; no recorded pass is NOT_COLLECTED, never IDLE."""
    status = inventory_pass.get("status") if isinstance(inventory_pass, dict) else None
    return status if status in ("COMPLETE", "PARTIAL", "FAILED") else "NOT_COLLECTED"
