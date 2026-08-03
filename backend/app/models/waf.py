"""Phase B — WAF endpoint entities, observations, baselines and incidents.

WAF-specific tables rather than a generic entity representation on the Phase A
tables. The alternative was considered and rejected in
docs/PHASE-B-WAF-BEHAVIOR.md section 15.1: moving stable, typed, indexed Phase A
columns into an opaque `entity_key` to claim genericity would break every Phase A
foreign key and query for a phase that currently has ten entities, and would
trade real typed contracts for a JSON blob. Phase A stays untouched, so a Phase B
defect cannot regress a live-validated pipeline.

The natural key is
``(instance, log_source, virtual_host, normalized_endpoint, http_method)``
with ``virtual_host`` NULLABLE and compared NULLS NOT DISTINCT — see
``WafEndpoint`` for why that matters more than it looks.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    EvidenceStatus,
    Severity,
    WafAnomalyType,
)


class WafEndpoint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One monitored WAF endpoint: the entity a baseline is built per.

    ``virtual_host`` is nullable because no WAF DSM verified so far exposes it.
    Nullable is the honest representation of "this source cannot tell us which
    application this was"; an empty-string sentinel is forbidden, because "" is
    a *value* and two different hostless applications would then look like one
    deliberate match rather than one admitted gap.

    That choice creates a uniqueness hazard which the migration fixes rather
    than this class: in standard SQL, NULL != NULL, so a UNIQUE index over a
    nullable column lets `(ls, NULL, '/login', 'GET')` be inserted an unbounded
    number of times. Every re-collection would mint a new entity, every entity
    would hold one observation, and no baseline would ever reach its minimum
    sample count. The migration therefore declares the constraint
    ``NULLS NOT DISTINCT`` (PostgreSQL 15+), so hostless observations converge
    on exactly one logical entity. `uq_waf_endpoint_natural_key` below carries
    the same columns for metadata/autogenerate purposes; the migration is what
    makes the NULL semantics correct, and an integration test asserts it.
    """

    __tablename__ = "waf_endpoint"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "log_source_id",
            "virtual_host",
            "normalized_endpoint",
            "http_method",
            name="uq_waf_endpoint_natural_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_waf_endpoint_active", "log_source_id", "is_active"),
        CheckConstraint(
            "virtual_host IS NULL OR length(virtual_host) > 0",
            name="virtual_host_not_empty",
        ),
        CheckConstraint("length(normalized_endpoint) > 0", name="endpoint_not_empty"),
        CheckConstraint("length(http_method) > 0", name="method_not_empty"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        nullable=False,
    )
    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: NULL when the WAF source exposes no virtual host. Never "".
    virtual_host: Mapped[str | None] = mapped_column(String(255))
    normalized_endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    http_method: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Which normalizer produced `normalized_endpoint`. A rule change bumps this
    #: and makes existing cells stale rather than silently wrong.
    normalizer_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: Zero buckets are materialized only while active. An endpoint that stops
    #: being requested is retired rather than zero-filled forever -- see
    #: docs/PHASE-B-WAF-BEHAVIOR.md section 10.2.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    metrics: Mapped[list[WafEndpointMetric]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    baselines: Mapped[list[WafEndpointBaseline]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    anomalies: Mapped[list[WafAnomaly]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        host = self.virtual_host or "(no virtual host)"
        return f"{self.http_method} {self.normalized_endpoint} on {host}"

    def __repr__(self) -> str:
        return f"<WafEndpoint {self.http_method} {self.normalized_endpoint}>"


class WafEndpointMetric(Base):
    """One endpoint's observation over one bucket.

    TimescaleDB hypertable on `bucket_start`; the composite primary key includes
    the partitioning column because Timescale requires it.
    """

    __tablename__ = "waf_endpoint_metric"
    __table_args__ = (
        Index("ix_waf_endpoint_metric_entity_time", "waf_endpoint_id", "bucket_start"),
        CheckConstraint("request_count >= 0", name="request_count_non_negative"),
        CheckConstraint(
            "endpoint_coverage_ratio >= 0.0 AND endpoint_coverage_ratio <= 1.0",
            name="coverage_ratio_range",
        ),
    )

    waf_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("waf_endpoint.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    bucket_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    #: SUM(eventcount), never COUNT(*). QRadar coalesces identical events into
    #: one Ariel record carrying a multiplier; see commit ae9932a.
    request_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    #: Nullable on purpose. NULL means "this DSM cannot tell us", which is a
    #: different finding from a measured zero and must never be rendered as one.
    distinct_client_count: Mapped[int | None] = mapped_column(Integer)
    blocked_count: Mapped[int | None] = mapped_column(BigInteger)
    error_count: Mapped[int | None] = mapped_column(BigInteger)

    #: Fraction of this log source's events in this bucket that carried a
    #: parseable endpoint. A bucket where a third of the traffic had no path is
    #: not a whole observation of this endpoint, so it is PARTIAL.
    endpoint_coverage_ratio: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )

    completeness: Mapped[BucketCompleteness] = mapped_column(
        String(16), default=BucketCompleteness.COMPLETE, nullable=False
    )
    collection_source: Mapped[str | None] = mapped_column(String(32))
    #: Non-secret evidence of how the row was produced: AQL text, window bounds,
    #: row counts, truncation flags. Never credentials, tokens or headers.
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    collection_duration_ms: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    normalizer_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    endpoint: Mapped[WafEndpoint] = relationship(back_populates="metrics")

    @property
    def bucket_end(self) -> datetime:
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    @property
    def is_complete(self) -> bool:
        # `==`, not `is`: String-typed enum columns come back as plain str.
        return self.completeness == BucketCompleteness.COMPLETE


class WafEndpointBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Robust seasonal baseline for one (endpoint, metric, weekday, hour) cell.

    Same estimator and seasonality as Phase A, deliberately: median/MAD over
    ISO weekday x hour. Phase B adds only `normalizer_version` to the key, so a
    normalization change cannot silently blend incomparable observations.
    """

    __tablename__ = "waf_endpoint_baseline"
    __table_args__ = (
        UniqueConstraint(
            "waf_endpoint_id",
            "metric_name",
            "weekday",
            "hour",
            "normalizer_version",
            name="uq_waf_baseline_cell",
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_range"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="hour_range"),
    )

    waf_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("waf_endpoint.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)

    median: Mapped[float] = mapped_column(Float, nullable=False)
    mad: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    p05: Mapped[float | None] = mapped_column(Float)
    p95: Mapped[float | None] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_reliable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    baseline_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    observations: Mapped[list[float]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    completeness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    excluded_sample_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    exclusion_counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    endpoint: Mapped[WafEndpoint] = relationship(back_populates="baselines")


class WafDetectorState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hysteresis state per (endpoint, detector type).

    `last_interval_start` is what makes evaluation idempotent: re-processing a
    bucket that already folded into the counters is a no-op.
    """

    __tablename__ = "waf_detector_state"
    __table_args__ = (
        UniqueConstraint(
            "waf_endpoint_id", "detector_type", name="uq_waf_detector_state"
        ),
    )

    waf_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("waf_endpoint.id", ondelete="CASCADE"),
        nullable=False,
    )
    detector_type: Mapped[WafAnomalyType] = mapped_column(String(40), nullable=False)

    consecutive_anomalous: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    consecutive_healthy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.NORMAL, nullable=False
    )
    last_interval_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    active_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    open_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class WafAnomaly(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A detected deviation on one WAF endpoint.

    Column-for-column the Phase A `LogSourceAnomaly` shape with
    `waf_endpoint_id` replacing `log_source_id`, so the unified anomaly API can
    project both into one list without a translation layer.
    """

    __tablename__ = "waf_anomaly"
    __table_args__ = (
        Index("ix_waf_anomaly_state", "waf_endpoint_id", "detector_type", "state"),
        Index("ix_waf_anomaly_detected", "detected_at"),
    )

    waf_endpoint_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("waf_endpoint.id", ondelete="CASCADE"),
        nullable=False,
    )
    detector_type: Mapped[WafAnomalyType] = mapped_column(String(40), nullable=False)
    severity: Mapped[Severity] = mapped_column(String(16), nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.CANDIDATE, nullable=False
    )

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    deviation_score: Mapped[float | None] = mapped_column(Float)
    deviation_ratio: Mapped[float | None] = mapped_column(Float)
    robust_z: Mapped[float | None] = mapped_column(Float)
    absolute_delta: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    consecutive_buckets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    baseline_version: Mapped[int | None] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    explanation: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_status: Mapped[EvidenceStatus] = mapped_column(
        String(16), default=EvidenceStatus.NOT_REQUESTED, nullable=False
    )

    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(255))

    endpoint: Mapped[WafEndpoint] = relationship(back_populates="anomalies")
    transitions: Mapped[list[WafAnomalyTransition]] = relationship(
        back_populates="anomaly",
        cascade="all, delete-orphan",
        order_by="WafAnomalyTransition.occurred_at",
    )

    def __repr__(self) -> str:
        return f"<WafAnomaly {self.detector_type} state={self.state}>"


class WafAnomalyTransition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Auditable record of one WAF anomaly lifecycle transition.

    A separate table from `anomaly_state_transition` rather than a nullable
    second foreign key on it: the Phase A table's `anomaly_id` is NOT NULL and
    relaxing it to support two parents would weaken a constraint that currently
    guarantees every transition has one. See section 15.2 (OPEN-5) -- this is
    the low-risk half of that choice, taken deliberately so Phase B cannot
    regress Phase A's audit trail.
    """

    __tablename__ = "waf_anomaly_transition"
    __table_args__ = (
        Index("ix_waf_anomaly_transition", "anomaly_id", "occurred_at"),
    )

    anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("waf_anomaly.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_state: Mapped[AnomalyState | None] = mapped_column(String(24))
    to_state: Mapped[AnomalyState] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(
        String(64), default="waf-anomaly-engine", nullable=False
    )
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)

    anomaly: Mapped[WafAnomaly] = relationship(back_populates="transitions")
