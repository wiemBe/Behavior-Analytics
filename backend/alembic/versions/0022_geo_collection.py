"""D-10: geo collection, events per country, and the GEO_COLLECTION search family.

ADDITIVE. Creates the `geo_country_bucket` hypertable and seeds the cadence row of the
new search family (1 h). Phase A, WAF, IPS and catch-all tables and enums are unchanged.

Revision id 0022, chained after 0021, the IPS detector persistence.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "geo_country_bucket",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        # The uniqueness construction: one stored column that is the code or the remainder
        # kind, so the nullable pair needs neither an expression index nor two partial ones.
        sa.Column(
            "slot",
            sa.String(length=16),
            sa.Computed("COALESCE(country_code, remainder_kind)", persisted=True),
            nullable=False,
        ),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.CHAR(length=2), nullable=True),
        sa.Column("remainder_kind", sa.String(length=16), nullable=True),
        sa.Column("events", sa.BigInteger(), nullable=False),
        sa.Column("measurement", sa.String(length=16), nullable=False),
        sa.Column("query_version", sa.String(length=32), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "direction IN ('SOURCE', 'DESTINATION')",
            name=op.f("ck_geo_country_bucket_direction_known"),
        ),
        sa.CheckConstraint(
            "remainder_kind IS NULL OR remainder_kind IN "
            "('UNRESOLVED', 'OVER_CAP', 'UNPARSEABLE')",
            name=op.f("ck_geo_country_bucket_remainder_kind_known"),
        ),
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name=op.f("ck_geo_country_bucket_country_code_iso"),
        ),
        sa.CheckConstraint(
            "(country_code IS NULL) <> (remainder_kind IS NULL)",
            name=op.f("ck_geo_country_bucket_code_xor_remainder"),
        ),
        sa.CheckConstraint(
            "measurement IN ('EXACT', 'LOWER_BOUND')",
            name=op.f("ck_geo_country_bucket_measurement_known"),
        ),
        sa.CheckConstraint(
            "bucket_seconds > 0", name=op.f("ck_geo_country_bucket_bucket_seconds_positive")
        ),
        sa.CheckConstraint("events >= 0", name=op.f("ck_geo_country_bucket_events_non_negative")),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_geo_country_bucket_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id",
            "bucket_start",
            "direction",
            "slot",
            name=op.f("pk_geo_country_bucket"),
        ),
    )
    _create_hypertable()

    op.execute(
        sa.text(
            "INSERT INTO search_cadence (family, cadence_seconds, updated_at) "
            "VALUES ('GEO_COLLECTION', 3600, now()) ON CONFLICT (family) DO NOTHING"
        )
    )


def _create_hypertable() -> None:
    """Register geo_country_bucket with Timescale, when the extension exists.

    The composite primary key already includes bucket_start, which Timescale
    requires of a partitioning column.
    """
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    if not available:
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute(
        sa.text(
            "SELECT create_hypertable(:t, :c, "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        ).bindparams(t="geo_country_bucket", c="bucket_start")
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM search_cadence WHERE family = 'GEO_COLLECTION'"))
    op.drop_table("geo_country_bucket")
