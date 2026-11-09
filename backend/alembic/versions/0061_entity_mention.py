"""SOC-16a1: normalized entity mentions and contributor backfill index.

ADDITIVE. ``entity_mention`` is a regular PostgreSQL table, not a hypertable.
Kinds, roles and sources are text CHECKs so later slices can extend them with an
additive migration. The contributor index supports a later bounded backfill.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "'IP', 'HOST', 'USER'"
_ROLES = "'SOURCE_IP', 'DESTINATION_IP', 'HOST', 'USER'"
_SOURCES = (
    "'EXPLANATION', 'WAF_TOP', 'IPS_TOP', 'IPS_ENTITY', 'OFFENSE', "
    "'CATCHALL', 'PW_VALUE', 'LOG_SOURCE'"
)


def upgrade() -> None:
    op.create_table(
        "entity_mention",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("value_norm", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("ref_table", sa.String(length=64), nullable=False),
        sa.Column("ref_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurred_end", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"kind IN ({_KINDS})", name=op.f("ck_entity_mention_kind_known")
        ),
        sa.CheckConstraint(
            f"role IN ({_ROLES})", name=op.f("ck_entity_mention_role_known")
        ),
        sa.CheckConstraint(
            f"source IN ({_SOURCES})", name=op.f("ck_entity_mention_source_known")
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_entity_mention_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id",
            "kind",
            "value_norm",
            "source",
            "ref_table",
            "ref_id",
            "occurred_at",
            name=op.f("pk_entity_mention"),
        ),
    )
    op.create_index(
        "ix_entity_mention_lookup",
        "entity_mention",
        ["kind", "value_norm", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_explanation_contributor_dimension_value",
        "anomaly_explanation_contributor",
        ["dimension", "value"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_explanation_contributor_dimension_value",
        table_name="anomaly_explanation_contributor",
    )
    op.drop_index("ix_entity_mention_lookup", table_name="entity_mention")
    op.drop_table("entity_mention")
