"""How each stored geography bucket was collected (GEO-6).

ADDITIVE. Two nullable columns on ``geo_country_bucket``: ``country_source`` (``PROPERTY`` or
``LOOKUP``) and ``country_property`` (the QRadar property name, NULL in lookup mode). The
collector writes both on every bucket from the settings it used for that run.

Rows that exist now stay NULL: they were stored before the buckets recorded how, and a guess
(the current setting is not what 0.1.9 used) would be a claim the page cannot back. NULL reads
as "unknown". No default, no backfill, and the table is not rewritten.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("geo_country_bucket", sa.Column("country_source", sa.String(16), nullable=True))
    op.add_column(
        "geo_country_bucket", sa.Column("country_property", sa.String(255), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_geo_country_bucket_country_source_known"),
        "geo_country_bucket",
        "country_source IS NULL OR country_source IN ('PROPERTY', 'LOOKUP')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_geo_country_bucket_country_source_known"), "geo_country_bucket", type_="check"
    )
    op.drop_column("geo_country_bucket", "country_property")
    op.drop_column("geo_country_bucket", "country_source")
