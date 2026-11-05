"""Analyst triage state and activity log (SOC-1a).

ADDITIVE. Two new tables; no Phase A table, enum or existing migration is touched.

`anomaly_triage` holds one analyst-owned state per anomaly, keyed by the polymorphic
pair `(family, anomaly_id)`, unique. `family` is a closed set of the four anomaly
tables (`log_source`, `waf`, `ips`, `property_watch`). There is deliberately no
foreign key: one column cannot reference four parents. The consequence is handled by
retention, not by a database cascade: each anomaly retention task deletes the triage
rows of the anomalies it removed (bounded batch), so this table ages out with the
incidents it describes instead of accumulating orphans forever.

An anomaly with no row reads as `NEW`, unowned, version 0. Nothing is backfilled: a
backfilled row would claim an analyst touched an incident nobody has touched.

`anomaly_triage_event` is the append-only activity log. A comment is an event of kind
`COMMENT`; its text lives only on this table's `comment` column. A PATCH that changes
two fields writes two events at the same `triage_version`, so a client can group one
action's changes without another column.

The defaults (status NEW, version 1) live in the model, not in the table: the
migration suite pins that no server default outlives a revision except the timestamps.

Downgrade drops the event table first (it references the triage table) and then the
triage table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAMILIES = "'log_source', 'waf', 'ips', 'property_watch'"
_STATUSES = "'NEW', 'IN_PROGRESS', 'CLOSED'"
_DISPOSITIONS = (
    "'TRUE_POSITIVE', 'BENIGN_TRUE_POSITIVE', 'NOISE', 'COLLECTION_PROBLEM', "
    "'KNOWN_CHANGE'"
)
_KINDS = "'STATUS', 'OWNER', 'DISPOSITION', 'NOTE', 'COMMENT'"


def upgrade() -> None:
    op.create_table(
        "anomaly_triage",
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("anomaly_id", sa.UUID(), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=True),
        sa.Column("disposition_note", sa.String(length=1000), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            f"family IN ({_FAMILIES})",
            name=op.f("ck_anomaly_triage_family_known"),
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})",
            name=op.f("ck_anomaly_triage_status_known"),
        ),
        sa.CheckConstraint(
            f"disposition IS NULL OR disposition IN ({_DISPOSITIONS})",
            name=op.f("ck_anomaly_triage_disposition_known"),
        ),
        sa.CheckConstraint(
            "status <> 'CLOSED' OR disposition IS NOT NULL",
            name=op.f("ck_anomaly_triage_closed_has_disposition"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_anomaly_triage_version_positive"),
        ),
        sa.CheckConstraint(
            "disposition_note IS NULL OR char_length(disposition_note) <= 1000",
            name=op.f("ck_anomaly_triage_disposition_note_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_triage")),
        sa.UniqueConstraint(
            "family",
            "anomaly_id",
            name=op.f("uq_anomaly_triage_family_anomaly_id"),
        ),
    )
    op.create_index(
        "ix_anomaly_triage_status", "anomaly_triage", ["status"], unique=False
    )
    op.create_index(
        "ix_anomaly_triage_owner", "anomaly_triage", ["owner"], unique=False
    )
    op.create_table(
        "anomaly_triage_event",
        sa.Column("triage_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("from_value", sa.String(length=255), nullable=True),
        sa.Column("to_value", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column("triage_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            f"kind IN ({_KINDS})",
            name=op.f("ck_anomaly_triage_event_kind_known"),
        ),
        sa.CheckConstraint(
            "comment IS NULL OR char_length(comment) <= 2000",
            name=op.f("ck_anomaly_triage_event_comment_length"),
        ),
        sa.CheckConstraint(
            "kind <> 'COMMENT' OR (comment IS NOT NULL AND char_length(comment) > 0)",
            name=op.f("ck_anomaly_triage_event_comment_present"),
        ),
        sa.CheckConstraint(
            "triage_version >= 1",
            name=op.f("ck_anomaly_triage_event_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["triage_id"],
            ["anomaly_triage.id"],
            name=op.f("fk_anomaly_triage_event_triage_id_anomaly_triage"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_triage_event")),
    )
    op.create_index(
        "ix_anomaly_triage_event_triage",
        "anomaly_triage_event",
        ["triage_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_anomaly_triage_event_triage", table_name="anomaly_triage_event")
    op.drop_table("anomaly_triage_event")
    op.drop_index("ix_anomaly_triage_owner", table_name="anomaly_triage")
    op.drop_index("ix_anomaly_triage_status", table_name="anomaly_triage")
    op.drop_table("anomaly_triage")
