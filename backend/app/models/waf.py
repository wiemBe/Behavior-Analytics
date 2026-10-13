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
    WafMeasurement,
    WafProfileValidationStatus,
    WafTelemetryStatus,
)

#: Width of a metric-semantics identity. See app/waf/semantics.py.
METRIC_SEMANTICS_LENGTH = 160


class WafSourceProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-log-source WAF configuration: the logical-to-property field mapping.

    Property names are site-authored QRadar display names, so they are data,
    never code. A NULL property means the logical field is unavailable on this
    source by configuration -- which is not an empty string, not zero, not a
    parse failure and not an absence of events.

    `profile_version` increments on every mapping change, and a change resets
    validation: collection resumes only once the new mapping validates.
    Validation evidence records counts and bounded enumerations only, never raw
    URLs, user agents or other request values.
    """

    __tablename__ = "waf_source_profile"
    __table_args__ = (
        UniqueConstraint("log_source_id", name="uq_waf_source_profile_log_source"),
        CheckConstraint("profile_version >= 1", name="profile_version_positive"),
        CheckConstraint("length(url_property) > 0", name="url_property_not_empty"),
        CheckConstraint("length(method_property) > 0", name="method_property_not_empty"),
        CheckConstraint(
            "virtual_host_property IS NULL OR length(virtual_host_property) > 0",
            name="virtual_host_property_not_empty",
        ),
        CheckConstraint(
            "action_property IS NULL OR length(action_property) > 0",
            name="action_property_not_empty",
        ),
        CheckConstraint(
            "response_code_property IS NULL OR length(response_code_property) > 0",
            name="response_code_property_not_empty",
        ),
        CheckConstraint(
            "rule_id_property IS NULL OR length(rule_id_property) > 0",
            name="rule_id_property_not_empty",
        ),
        CheckConstraint(
            "user_agent_property IS NULL OR length(user_agent_property) > 0",
            name="user_agent_property_not_empty",
        ),
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
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Operator note naming the product or DSM. Informational; never parsed.
    product_label: Mapped[str | None] = mapped_column(String(128))

    url_property: Mapped[str] = mapped_column(String(64), nullable=False)
    method_property: Mapped[str] = mapped_column(String(64), nullable=False)
    virtual_host_property: Mapped[str | None] = mapped_column(String(64))
    action_property: Mapped[str | None] = mapped_column(String(64))
    response_code_property: Mapped[str | None] = mapped_column(String(64))
    rule_id_property: Mapped[str | None] = mapped_column(String(64))
    user_agent_property: Mapped[str | None] = mapped_column(String(64))
    #: Site-authored action values by outcome: ``{"BLOCKED": [...], "ALLOWED":
    #: [...]}``. Part of the mapping, so editing it bumps `profile_version`.
    #: Blocking is judged only through this map -- an HTTP 403 is a response code,
    #: not proof the WAF blocked anything -- and a value in neither list is
    #: counted as unmapped, never as allowed.
    action_value_map: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    validation_status: Mapped[WafProfileValidationStatus] = mapped_column(
        String(16), default=WafProfileValidationStatus.UNVALIDATED, nullable=False
    )
    #: The profile version the last validation ran against. Collection requires
    #: it to equal `profile_version`.
    validated_profile_version: Mapped[int | None] = mapped_column(Integer)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_report: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    validation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_requested_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class WafSourceBucket(Base):
    """What one WAF source's collection established about one bucket.

    One row per (log source, bucket), written for every successfully collected
    bucket even when no endpoint row is -- which is exactly when it matters: a
    bucket in which every endpoint was refused admission still records that
    high-cardinality traffic existed. Holds the source-level facts the endpoint
    rows used to copy into each row's provenance, the admission outcome, and the
    maintenance decision made at collection time.

    It is also what gives an *absent* endpoint row meaning. Completeness is
    recorded per dimension rather than as one flag:

      * source volume -- `total_events` is one ungrouped aggregate, so it is
        exact whenever the row exists, whatever happened to grouping;
      * endpoint grouping and new-endpoint discovery -- complete unless
        `groups_truncated`;
      * endpoint counts -- `request_count_measurement`: EXACT, or LOWER_BOUND
        when grouping was truncated or coverage fell below its floor;
      * composition -- `distinct_client_count` on a row is always a lower bound
        (the maximum over folded raw groups).

    With `sparse_endpoint_rows`, a tracked endpoint with no row in this bucket
    served zero requests if and only if the counts are EXACT; otherwise its count
    is unknown. Buckets collected before sparse storage wrote explicit zero rows
    and carry `sparse_endpoint_rows = false`, where absence means "not tracked".

    TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "waf_source_bucket"
    __table_args__ = (
        CheckConstraint(
            "endpoint_coverage_ratio >= 0.0 AND endpoint_coverage_ratio <= 1.0",
            name="coverage_ratio_range",
        ),
        CheckConstraint("total_events >= 0", name="total_events_non_negative"),
        CheckConstraint("untracked_requests >= 0", name="untracked_requests_non_negative"),
        CheckConstraint(
            "request_count_measurement IN ('EXACT', 'LOWER_BOUND')",
            name="request_count_measurement_known",
        ),
        CheckConstraint(
            "action_measurement IN ('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'FAILED')",
            name="action_measurement_known",
        ),
        CheckConstraint(
            "response_code_measurement IN ('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'FAILED')",
            name="response_code_measurement_known",
        ),
        CheckConstraint(
            "client_measurement IN ('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'FAILED')",
            name="client_measurement_known",
        ),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_semantics: Mapped[str] = mapped_column(
        String(METRIC_SEMANTICS_LENGTH), nullable=False
    )

    completeness: Mapped[BucketCompleteness] = mapped_column(String(16), nullable=False)
    endpoint_coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    total_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parsed_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    group_count: Mapped[int] = mapped_column(Integer, nullable=False)
    groups_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    unusable_groups: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Distinct normalized (virtual host, endpoint, method) keys observed.
    observed_endpoints: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Of those, already active and tracked -- never subject to admission.
    tracked_endpoints_observed: Mapped[int] = mapped_column(Integer, nullable=False)
    admitted_endpoints: Mapped[int] = mapped_column(Integer, nullable=False)
    below_admission_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_over_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Observed keys that became no entity: below threshold plus over quota.
    untracked_endpoints: Mapped[int] = mapped_column(Integer, nullable=False)
    untracked_requests: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: True when eligible new endpoints were refused for want of quota.
    admission_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Explicit zero rows written for tracked endpoints absent from the result.
    #: Always 0 for a sparse bucket, which materializes none.
    zero_filled_endpoints: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Whether every endpoint count in this bucket is exact or a lower bound.
    request_count_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), nullable=False
    )
    #: Tracked endpoints absent from the result have no row; see the class
    #: docstring for what that absence means.
    sparse_endpoint_rows: Mapped[bool] = mapped_column(Boolean, nullable=False)

    #: The maintenance decision at collection time. Persisted so a bucket that
    #: was in maintenance stays excluded after the flag is cleared.
    in_maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False)

    #: Phase B.2 breakdowns, each its own bounded search with its own
    #: completeness. Exact endpoint volume says nothing about whether the action
    #: or response-code breakdown was whole, so each is recorded separately.
    #: NOT_COLLECTED or FAILED: every endpoint value of that breakdown is NULL.
    action_measurement: Mapped[WafTelemetryStatus] = mapped_column(
        String(16), default=WafTelemetryStatus.NOT_COLLECTED, nullable=False
    )
    action_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))
    #: Events in the window carrying an action value, from the action search.
    action_events: Mapped[int | None] = mapped_column(BigInteger)
    response_code_measurement: Mapped[WafTelemetryStatus] = mapped_column(
        String(16), default=WafTelemetryStatus.NOT_COLLECTED, nullable=False
    )
    response_code_semantics: Mapped[str | None] = mapped_column(
        String(METRIC_SEMANTICS_LENGTH)
    )
    response_code_events: Mapped[int | None] = mapped_column(BigInteger)
    client_measurement: Mapped[WafTelemetryStatus] = mapped_column(
        String(16), default=WafTelemetryStatus.NOT_COLLECTED, nullable=False
    )
    client_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))
    client_events: Mapped[int | None] = mapped_column(BigInteger)

    collection_source: Mapped[str | None] = mapped_column(String(32))
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    collection_duration_ms: Mapped[int | None] = mapped_column(Integer)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def bucket_end(self) -> datetime:
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    @property
    def discovery_complete(self) -> bool:
        """Every endpoint with traffic in the window was visible to admission."""
        return not self.groups_truncated

    @property
    def coverage_measurement(self) -> WafMeasurement:
        """`endpoint_coverage_ratio` is a lower bound when grouping was truncated."""
        return WafMeasurement.LOWER_BOUND if self.groups_truncated else WafMeasurement.EXACT


def _default_tracked_since(context) -> datetime:  # type: ignore[no-untyped-def]
    return context.get_current_parameters()["first_seen_at"]


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
            # The collector looks endpoints up per normalizer version, so the
            # key must be per version too: without it, the first version bump
            # collides with every unchanged endpoint (migration 0007).
            "normalizer_version",
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
    #: Start of the first bucket of the current tracking period: the bucket that
    #: created or last reactivated the entity. `first_seen_at` is kept across a
    #: reactivation for novelty; this is not.
    tracked_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_default_tracked_since
    )
    #: Start of the last bucket collected while the entity was tracked, set when
    #: it is retired. NULL while active.
    tracked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Wall-clock time of this endpoint's last baseline rebuild. Scheduled
    #: rebuilds take the least recently rebuilt endpoints first; an endpoint
    #: whose rebuild wrote no cell still moves to the back of the queue, so it
    #: cannot starve the others.
    baseline_rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    metrics: Mapped[list[WafEndpointMetric]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    baselines: Mapped[list[WafEndpointBaseline]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )
    anomalies: Mapped[list[WafAnomaly]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )

    def tracked_at(self, bucket_start: datetime) -> bool:
        """Whether collection of `bucket_start` would have written this entity's row.

        Only then can the absence of a row in a sparse, exact bucket be read as a
        measured zero. Before `tracked_since` or after `tracked_until` the entity
        was not tracked, and low-volume traffic to it may have been refused
        admission without a row.
        """
        if bucket_start < self.tracked_since:
            return False
        return self.tracked_until is None or bucket_start <= self.tracked_until

    @property
    def display_name(self) -> str:
        host = self.virtual_host or "(no virtual host)"
        return f"{self.http_method} {self.normalized_endpoint} on {host}"

    def __repr__(self) -> str:
        return f"<WafEndpoint {self.http_method} {self.normalized_endpoint}>"


class WafEndpointMetric(Base):
    """One endpoint's observation over one bucket.

    TimescaleDB hypertable on `bucket_start`; the composite primary key includes
    the partitioning column because Timescale requires it, and it is also the
    `(endpoint, time)` access path -- a second index on the same columns only
    doubled index storage (dropped in migration 0009).

    Rows are sparse and slim. A row exists only for an endpoint that the grouped
    result mentioned; absence is interpreted through the bucket's
    `WafSourceBucket` record. Facts that are the same for every row of a bucket
    -- provenance, collection time, watermark, metric-semantics identity -- live
    on that record and are left NULL or empty here; a row's identity is its own
    `metric_semantics` when set (rows written before 0009) and otherwise its
    bucket's. Re-collecting a bucket replaces all of its rows, so a row can never
    outlive the bucket record it inherits from.
    """

    __tablename__ = "waf_endpoint_metric"
    __table_args__ = (
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
    #: Requests whose action is in the profile's BLOCKED list. Collected by the
    #: action breakdown; NULL when that breakdown was not collected.
    blocked_count: Mapped[int | None] = mapped_column(BigInteger)
    #: Legacy, never written. The response-code breakdown writes the two classes
    #: separately below, because 4xx and 5xx are different conditions.
    error_count: Mapped[int | None] = mapped_column(BigInteger)

    # --- Phase B.2 breakdowns. Semantics per the bucket's *_measurement. ------
    #: Requests carrying any action value: the block-ratio denominator.
    action_events: Mapped[int | None] = mapped_column(BigInteger)
    #: Requests whose action value is in neither list of the map. Any of them may
    #: have been a block, so while nonzero `blocked_count` is a lower bound.
    unmapped_action_count: Mapped[int | None] = mapped_column(BigInteger)
    #: Requests carrying any response-code value: the error-ratio denominator.
    response_code_events: Mapped[int | None] = mapped_column(BigInteger)
    client_error_count: Mapped[int | None] = mapped_column(BigInteger)
    server_error_count: Mapped[int | None] = mapped_column(BigInteger)
    #: Response-code values that are not a three-digit status. Any may have been
    #: a 4xx or 5xx, so while nonzero both class counts are lower bounds.
    unclassified_response_count: Mapped[int | None] = mapped_column(BigInteger)
    #: Requests attributed to a source IP: the concentration denominator.
    client_events: Mapped[int | None] = mapped_column(BigInteger)
    #: Requests from the endpoint's largest source IP, and its five largest.
    top1_client_requests: Mapped[int | None] = mapped_column(BigInteger)
    top5_client_requests: Mapped[int | None] = mapped_column(BigInteger)
    #: Distinct source IPs among the endpoint's requests.
    client_sources: Mapped[int | None] = mapped_column(Integer)
    #: Up to five `{"source_ip", "requests"}` contributors, stored only when the
    #: largest reaches the concentration materiality floor. Evidence, bounded.
    top_clients: Mapped[list | None] = mapped_column(JSONB)

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
    #: What `request_count` means for this row. NULL for rows collected before
    #: identities were recorded, and NULL matches nothing: such rows never train
    #: or judge against a baseline. See app/waf/semantics.py.
    metric_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))

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
    #: Ratio metrics only: the pooled numerator and denominator over every
    #: training bucket in the cell. The expected proportion is their quotient --
    #: an aggregate proportion, not an average of bucket ratios, so a bucket with
    #: two requests weighs two requests, not one bucket.
    numerator_sum: Mapped[float | None] = mapped_column(Float)
    denominator_sum: Mapped[float | None] = mapped_column(Float)
    excluded_sample_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    exclusion_counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: The metric-semantics identity every sample in this cell shared. A bucket
    #: whose identity differs is judged INSUFFICIENT_DATA against this cell.
    metric_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))

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
    #: Whether `observed_value` is an exact count or a lower bound. A lower bound
    #: must be rendered "at least n", never as the count.
    observed_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), default=WafMeasurement.EXACT, nullable=False
    )

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
    observed_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), default=WafMeasurement.EXACT, nullable=False
    )

    anomaly: Mapped[WafAnomaly] = relationship(back_populates="transitions")
