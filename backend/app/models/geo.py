"""Geography (D-10): events per country, for the home map.

One row per measured (instance, bucket, direction, country or remainder). A bucket that
was not measured (failed, rejected, over budget) has no row, so absence is never a zero.
The raw lookup string is parsed in the fold and never stored; no address is ever selected.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GeoCountryBucket(Base):
    """What the geo search established about one instance, bucket and direction.

    A country row has `country_code` and no `remainder_kind`; a remainder row has the
    reverse (`UNRESOLVED`: private, internal or unknown to GeoIP; `OVER_CAP`: countries
    beyond the per-bucket cap; `UNPARSEABLE`: a key that was not a lookup string).
    `measurement` is LOWER_BOUND when the search was truncated at one bucket, and then
    the events are lower bounds.

    The key is (instance, bucket, direction, `slot`), where `slot` is the stored
    ``COALESCE(country_code, remainder_kind)``: a plain column the nullable pair cannot
    break, which `ON CONFLICT` can name. TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "geo_country_bucket"
    __table_args__ = (
        CheckConstraint("direction IN ('SOURCE', 'DESTINATION')", name="direction_known"),
        CheckConstraint(
            "remainder_kind IS NULL OR remainder_kind IN "
            "('UNRESOLVED', 'OVER_CAP', 'UNPARSEABLE')",
            name="remainder_kind_known",
        ),
        CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="country_code_iso"
        ),
        CheckConstraint(
            "(country_code IS NULL) <> (remainder_kind IS NULL)",
            name="code_xor_remainder",
        ),
        CheckConstraint("measurement IN ('EXACT', 'LOWER_BOUND')", name="measurement_known"),
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
        CheckConstraint("events >= 0", name="events_non_negative"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    direction: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: The country code, or the remainder kind: always set, never written by the app.
    slot: Mapped[str] = mapped_column(
        String(16),
        Computed("COALESCE(country_code, remainder_kind)", persisted=True),
        primary_key=True,
    )
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    remainder_kind: Mapped[str | None] = mapped_column(String(16))
    events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    query_version: Mapped[str] = mapped_column(String(32), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
