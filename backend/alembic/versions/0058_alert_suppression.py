"""SOC-17d: time-boxed alert suppression and its per-incident hit record.

ADDITIVE. Two new tables; no existing table or enum is touched and no new SQL
enum is created (``kind`` reuses the Python ``SuppressionKind``, the family
column reuses ``AnomalyFamily``, both as CHECKs — AGENTS.md rule 6).

``alert_suppression`` holds at most one open row per
``(family, subject_id, detector_type)`` through a partial unique index over
``ended_at IS NULL`` with ``NULLS NOT DISTINCT``. The index cannot test time (a
partial-index predicate must be immutable), so the create/extend service closes
a naturally expired row in the same transaction as the new one; that is
application discipline, not a schema guarantee. ``alert_suppression_hit`` is one
row per suppressed incident (unique ``(suppression_id, anomaly_id)``) and goes
with its suppression (cascade).

No FK to the four subject tables, or from the hit to the incident: the family
names the table (the ``anomaly_triage`` precedent).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAMILIES = "'log_source', 'waf', 'ips', 'property_watch'"
_KINDS = "'ANALYST', 'KNOWN_CHANGE'"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "alert_suppression",
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("detector_type", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.String(length=255), nullable=True),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"family IN ({_FAMILIES})", name=op.f("ck_alert_suppression_family_known")
        ),
        sa.CheckConstraint(f"kind IN ({_KINDS})", name=op.f("ck_alert_suppression_kind_known")),
        sa.CheckConstraint(
            "detector_type IS NULL OR length(detector_type) > 0",
            name=op.f("ck_alert_suppression_detector_type_not_empty"),
        ),
        sa.CheckConstraint(
            "length(reason) > 0", name=op.f("ck_alert_suppression_reason_not_empty")
        ),
        sa.CheckConstraint(
            "kind <> 'KNOWN_CHANGE' OR (reference IS NOT NULL AND length(reference) > 0)",
            name=op.f("ck_alert_suppression_known_change_has_reference"),
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name=op.f("ck_alert_suppression_ends_after_start")
        ),
        sa.CheckConstraint(
            "ends_at <= starts_at + interval '30 days'",
            name=op.f("ck_alert_suppression_ends_within_30_days"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_suppression")),
    )
    op.create_index(
        "uq_alert_suppression_open_condition",
        "alert_suppression",
        ["family", "subject_id", "detector_type"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "alert_suppression_hit",
        sa.Column("suppression_id", sa.UUID(), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("anomaly_id", sa.UUID(), nullable=False),
        sa.Column("detector_type", sa.String(length=40), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"family IN ({_FAMILIES})",
            name=op.f("ck_alert_suppression_hit_family_known"),
        ),
        sa.CheckConstraint(
            "length(detector_type) > 0",
            name=op.f("ck_alert_suppression_hit_detector_type_not_empty"),
        ),
        sa.ForeignKeyConstraint(
            ["suppression_id"],
            ["alert_suppression.id"],
            name=op.f("fk_alert_suppression_hit_suppression_id_alert_suppression"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_suppression_hit")),
        sa.UniqueConstraint(
            "suppression_id", "anomaly_id", name="uq_alert_suppression_hit"
        ),
    )


def downgrade() -> None:
    op.drop_table("alert_suppression_hit")
    op.drop_index(
        "uq_alert_suppression_open_condition", table_name="alert_suppression"
    )
    op.drop_table("alert_suppression")
