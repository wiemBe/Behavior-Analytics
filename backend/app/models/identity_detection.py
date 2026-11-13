"""SOC-10 identity profiles and the per-bucket aggregates the fold may store.

Raw usernames live only inside the pure fold. What leaves it, and what these
tables hold, is the per-source, per-catalog-event-ID, per-bucket top-N (N = 20)
plus the event-ID counts those lists explain. A 4625 or 4740 username below the
admission floor is not a row. No per-user baseline.

`identity_bucket`, `identity_event_count` and `identity_top_username` are
TimescaleDB hypertables on `bucket_start`. Retention is a later bounded delete
of 35 days for counts and the top-N; this migration installs no policy, the same
way the WAF and IPS bucket migrations do not. Anomaly tables are a later slice.
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
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    IDENTITY_CATALOG_VERSION,
    IDENTITY_EVENT_CATALOG,
    IDENTITY_RARE_NAME_FLOOR,
    IDENTITY_TOP_N,
    IDENTITY_USERNAME_MAX_LENGTH,
    IDENTITY_VOLUME_NAME_FLOOR,
    IdentityEventKind,
    IdentityMeasurement,
    IdentityProfileValidationStatus,
)

_CATALOG_IDS = ", ".join(str(event_id) for event_id, _kind in IDENTITY_EVENT_CATALOG)
_VOLUME_IDS = ", ".join(
    str(event_id)
    for event_id, kind in IDENTITY_EVENT_CATALOG
    if kind is IdentityEventKind.VOLUME
)
_MEASUREMENTS = ", ".join(f"'{member.value}'" for member in IdentityMeasurement)
_VALIDATION = ", ".join(f"'{member.value}'" for member in IdentityProfileValidationStatus)

#: A count the search did not run is NULL, never 0. An exact count is present,
#: including a measured zero. A truncated count may be the observed floor or unknown.
_COUNT_HONEST = (
    "(measurement <> 'NOT_COLLECTED' OR event_count IS NULL) "
    "AND (measurement <> 'EXACT' OR event_count IS NOT NULL) "
    "AND (event_count IS NULL OR event_count >= 0)"
)
_POPULATION_HONEST = (
    "(measurement <> 'NOT_COLLECTED' OR population IS NULL) "
    "AND (measurement <> 'EXACT' OR population IS NOT NULL) "
    "AND (population IS NULL OR population >= 0)"
)
_EVENT_ID_KNOWN = f"event_id IN ({_CATALOG_IDS})"
#: Rare IDs store from one event. Volume IDs store only at the admission floor.
_NAME_FLOOR = (
    f"event_count >= {IDENTITY_RARE_NAME_FLOOR} "
    f"AND (event_id NOT IN ({_VOLUME_IDS}) OR event_count >= {IDENTITY_VOLUME_NAME_FLOOR})"
)


def _default_enabled_event_ids() -> list[int]:
    """Every catalog ID. A source disables one by saving a shorter list."""
    return [event_id for event_id, _kind in IDENTITY_EVENT_CATALOG]


class IdentitySourceProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One versioned mapping for one identity log source. WAF profile shape.

    `enabled` stays false until validation matches `profile_version`. The
    collector of a later slice enforces that; the row can exist before it.
    """

    __tablename__ = "identity_source_profile"
    __table_args__ = (
        CheckConstraint("profile_version >= 1", name="profile_version_positive"),
        CheckConstraint("length(event_id_property) > 0", name="event_id_property_not_empty"),
        CheckConstraint("length(username_property) > 0", name="username_property_not_empty"),
        CheckConstraint(
            "logon_type_property IS NULL OR length(logon_type_property) > 0",
            name="logon_type_property_not_empty",
        ),
        CheckConstraint(
            f"validation_status IN ({_VALIDATION})",
            name="validation_status_known",
        ),
        CheckConstraint(
            "jsonb_typeof(enabled_event_ids) = 'array'",
            name="enabled_event_ids_array",
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
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    product_label: Mapped[str | None] = mapped_column(String(128))
    #: Logical EVENT_ID field: a custom property, or the normalized name the lab encodes.
    event_id_property: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Logical USERNAME field. The only normalized string a watch may name is `username`.
    username_property: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Optional and unused by this family. NULL means the source has no mapping for it.
    logon_type_property: Mapped[str | None] = mapped_column(String(64))
    #: Catalog event IDs this source collects. A shorter list disables the rest.
    enabled_event_ids: Mapped[list[int]] = mapped_column(
        JSONB, default=_default_enabled_event_ids, nullable=False
    )
    validation_status: Mapped[str] = mapped_column(
        String(16), default=IdentityProfileValidationStatus.UNVALIDATED, nullable=False
    )
    validated_profile_version: Mapped[int | None] = mapped_column(Integer)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_report: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    validation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_requested_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class IdentityBucket(Base):
    """What the counts search established for one identity source and bucket.

    One row per (log source, bucket), including a bucket whose every username
    was refused admission. `population` is the sum of the observed catalog-event
    counts: NULL when the counts search did not run, never a stand-in zero.
    `measurement` is EXACT only when that search was whole.

    TimescaleDB hypertable on `bucket_start`. Counts and the top-N are kept
    `IDENTITY_COUNT_RETENTION_DAYS` days by a later deleter, not by a policy
    this migration installs.
    """

    __tablename__ = "identity_bucket"
    __table_args__ = (
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
        CheckConstraint("catalog_version >= 1", name="catalog_version_positive"),
        CheckConstraint(f"measurement IN ({_MEASUREMENTS})", name="measurement_known"),
        CheckConstraint(_POPULATION_HONEST, name="population_honest"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_version: Mapped[int] = mapped_column(
        Integer, default=IDENTITY_CATALOG_VERSION, nullable=False
    )
    measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    population: Mapped[int | None] = mapped_column(BigInteger)
    #: True when a name search that feeds this bucket was cut. Novelty must not
    #: treat the stored top-N as the whole population.
    admission_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentityEventCount(Base):
    """The event-ID count for one source, catalog event ID and bucket.

    `event_count` is NULL when that count was not measured. A zero is stored
    only for an EXACT search that did not mention the key.

    TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "identity_event_count"
    __table_args__ = (
        CheckConstraint(_EVENT_ID_KNOWN, name="event_id_known"),
        CheckConstraint(f"measurement IN ({_MEASUREMENTS})", name="measurement_known"),
        CheckConstraint(
            f"names_measurement IN ({_MEASUREMENTS})", name="names_measurement_known"
        ),
        CheckConstraint(_COUNT_HONEST, name="count_honest"),
        CheckConstraint(
            "names_measurement <> 'NOT_COLLECTED' OR NOT admission_truncated",
            name="unrun_names_not_truncated",
        ),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    event_count: Mapped[int | None] = mapped_column(BigInteger)
    measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    names_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    admission_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class IdentityTopUsername(Base):
    """One admitted username in a bucket's top-N for one catalog event ID.

    `truncated` is the length cap, not the search cap: the text was longer than
    255 characters. Rank 1 is the highest count; ties break by username ascending.
    The volume-floor check is the allow-list at rest: a 4625 or 4740 name below
    3 events cannot be inserted.

    TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "identity_top_username"
    __table_args__ = (
        CheckConstraint(_EVENT_ID_KNOWN, name="event_id_known"),
        CheckConstraint(f"rank BETWEEN 1 AND {IDENTITY_TOP_N}", name="rank_in_top_n"),
        CheckConstraint("length(username) > 0", name="username_not_empty"),
        CheckConstraint(
            f"char_length(username) <= {IDENTITY_USERNAME_MAX_LENGTH}",
            name="username_width",
        ),
        CheckConstraint(_NAME_FLOOR, name="name_admission_floor"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(IDENTITY_USERNAME_MAX_LENGTH), nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    event_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
