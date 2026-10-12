"""Phase C.1 log-source fleet inventory and classification.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-12

Adds QRadar-owned inventory facts to ``log_source`` and separate tables for
types, groups, classification rules, and per-source review state. Existing
sources are backfilled with their platform ``created_at`` as ``first_seen_at``;
the temporary server default is then removed so upgraded and fresh schemas
match.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "log_source",
        sa.Column("qradar_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "log_source",
        sa.Column("qradar_modified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("log_source", sa.Column("auto_discovered", sa.Boolean(), nullable=True))
    op.add_column(
        "log_source",
        sa.Column("group_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("log_source", sa.Column("identifier", sa.String(255), nullable=True))
    op.add_column("log_source", sa.Column("is_internal", sa.Boolean(), nullable=True))
    op.add_column(
        "log_source",
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute("UPDATE log_source SET first_seen_at = created_at")
    op.execute('ALTER TABLE "log_source" ALTER COLUMN "first_seen_at" DROP DEFAULT')

    op.create_table(
        "log_source_type",
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("internal", sa.Boolean(), nullable=False),
        sa.Column("custom", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_log_source_type_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id", "type_id", name=op.f("pk_log_source_type")
        ),
    )
    op.create_table(
        "log_source_group",
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("qradar_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_qradar_id", sa.Integer(), nullable=True),
        sa.Column("assignable", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_log_source_group_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id", "qradar_id", name=op.f("pk_log_source_group")
        ),
    )
    op.create_table(
        "classification_rule",
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("pattern", sa.String(256), nullable=False),
        sa.Column("target_class", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_classification_rule_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_rule")),
        sa.UniqueConstraint(
            "instance_id",
            "kind",
            "pattern",
            name="uq_classification_rule_instance_kind_pattern",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "log_source_classification",
        sa.Column("log_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_class", sa.String(32), nullable=False),
        sa.Column("basis", sa.String(32), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("override_class", sa.String(32), nullable=True),
        sa.Column("override_by", sa.String(255), nullable=True),
        sa.Column("override_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "conflicts", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_log_source_classification_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["classification_rule.id"],
            name=op.f("fk_log_source_classification_rule_id_classification_rule"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id", name=op.f("pk_log_source_classification")
        ),
    )


def downgrade() -> None:
    op.drop_table("log_source_classification")
    op.drop_table("classification_rule")
    op.drop_table("log_source_group")
    op.drop_table("log_source_type")
    op.drop_column("log_source", "first_seen_at")
    op.drop_column("log_source", "is_internal")
    op.drop_column("log_source", "identifier")
    op.drop_column("log_source", "group_ids")
    op.drop_column("log_source", "auto_discovered")
    op.drop_column("log_source", "qradar_modified_at")
    op.drop_column("log_source", "qradar_created_at")
