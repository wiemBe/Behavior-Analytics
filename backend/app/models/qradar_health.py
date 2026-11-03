"""QRadar health in detail (D-42): stored windows of disk, system, pipeline and notifications.

One `qradar_health_run` row per (console, collected window) says what each part of the window
established. The bucket tables hold only bounded aggregates (rule 3): a window with no
measurement has no bucket row, so absence is never a zero, and the run row says why.
`qradar_health_rest_sample` is the REST half: one row per console per run.

All four are TimescaleDB hypertables on their time column.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: What a part of a window can be: collected, collected in part, never asked, or asked and failed.
PART_STATES: tuple[str, ...] = ("OK", "PARTIAL", "NOT_COLLECTED", "FAILED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class QRadarHealthRun(Base):
    """What one collection run established about one console's window.

    `window_start` .. `window_end` is the half-open window the two searches covered. A part's
    reason is a registry code (`qradar_health_detail.reason.*`) with its parameters, so the API
    renders the sentence and no upstream text is ever stored.
    """

    __tablename__ = "qradar_health_run"
    __table_args__ = (
        CheckConstraint(_in("metrics_state", PART_STATES), name="metrics_state_known"),
        CheckConstraint(_in("notifications_state", PART_STATES), name="notifications_state_known"),
        CheckConstraint("window_end > window_start", name="window_ordered"),
        CheckConstraint("cadence_seconds > 0", name="cadence_positive"),
        CheckConstraint(
            "unparseable_hosts >= 0 AND dropped_mounts >= 0 AND dropped_metrics >= 0 "
            "AND null_values >= 0 AND invalid_values >= 0 AND dropped_events >= 0",
            name="counters_non_negative",
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cadence_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    query_version: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics_state: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics_reason_code: Mapped[str | None] = mapped_column(String(96))
    metrics_reason_params: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    notifications_state: Mapped[str] = mapped_column(String(16), nullable=False)
    notifications_reason_code: Mapped[str | None] = mapped_column(String(96))
    notifications_reason_params: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    #: Distinct log-source names in the two searches that held no hostname.
    unparseable_hosts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Disk rows on a mount outside the allowlist.
    dropped_mounts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Rows of a metric outside the fixed list.
    dropped_metrics: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Rows with no usable value (NULL or not a number): dropped, never a zero.
    null_values: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Rows with a negative value or one above the metric's range.
    invalid_values: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Notification events in groups dropped for an unparseable host or no QID name.
    dropped_events: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class QRadarHealthMetricBucket(Base):
    """Max, average and sample count of one metric of one host over a window.

    `element` is the mount for a disk metric and the empty string for every other metric (it is
    part of the key, which cannot hold NULL). `host` is the hostname parsed from the log-source
    name. Values are as QRadar reports them: `DiskUsage` is a fraction from 0 to 1, bytes are
    bytes, queues are events.
    """

    __tablename__ = "qradar_health_metric_bucket"
    __table_args__ = (
        CheckConstraint("bucket_end > bucket_start", name="window_ordered"),
        CheckConstraint("max_value >= 0", name="max_value_non_negative"),
        CheckConstraint("samples >= 0", name="samples_non_negative"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    element: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_value: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    #: NULL when QRadar returned no average: unknown, never 0.
    avg_value: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    samples: Mapped[int] = mapped_column(BigInteger, nullable=False)


class QRadarHealthNotificationBucket(Base):
    """One QRadar system-notification group of a window: a (host, QID name) or the remainder.

    `GROUP` rows are the busiest groups of the run, at most 50. The single `REMAINDER` row sums
    the groups below them (`groups` of them), so the window's events still add up; it holds no
    host and no QID name.
    """

    __tablename__ = "qradar_health_notice_bucket"
    __table_args__ = (
        CheckConstraint(_in("kind", ("GROUP", "REMAINDER")), name="kind_known"),
        CheckConstraint(
            "kind = 'GROUP' OR (host = '' AND qid_name = '')", name="remainder_has_no_name"
        ),
        CheckConstraint("kind = 'REMAINDER' OR host <> ''", name="group_has_host"),
        CheckConstraint("bucket_end > bucket_start", name="window_ordered"),
        CheckConstraint("events >= 0 AND groups >= 1", name="counts_valid"),
        CheckConstraint(
            "max_severity IS NULL OR max_severity BETWEEN 0 AND 10", name="severity_range"
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    qid_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    groups: Mapped[int] = mapped_column(Integer, nullable=False)
    max_severity: Mapped[int | None] = mapped_column(SmallInteger)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QRadarHealthRestSample(Base):
    """The REST half of one run: what the 30 s health service read, once per run.

    `slot_start` is the cadence slot the run claimed, so the row is written at most once per
    run however often the health service is polled. A figure that was not read is NULL: a
    console that did not answer has `reachable` false and no latency, never a latency of 0.
    """

    __tablename__ = "qradar_health_rest_sample"
    __table_args__ = (
        CheckConstraint(
            "api_latency_ms IS NULL OR api_latency_ms >= 0", name="latency_non_negative"
        ),
        CheckConstraint(
            "(hosts_active IS NULL) = (hosts_total IS NULL) "
            "AND (hosts_active IS NULL OR (hosts_active >= 0 AND hosts_active <= hosts_total))",
            name="hosts_consistent",
        ),
        CheckConstraint(
            "ha_problem_count IS NULL OR ha_problem_count >= 0", name="ha_problems_non_negative"
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    slot_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reachable: Mapped[bool | None] = mapped_column(Boolean)
    api_latency_ms: Mapped[int | None] = mapped_column(Integer)
    hosts_active: Mapped[int | None] = mapped_column(Integer)
    hosts_total: Mapped[int | None] = mapped_column(Integer)
    ha_problem_count: Mapped[int | None] = mapped_column(Integer)
    license_overallocated: Mapped[bool | None] = mapped_column(Boolean)
