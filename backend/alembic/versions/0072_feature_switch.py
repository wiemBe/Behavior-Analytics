"""OPS-FLAGS-S2a: the operator-managed family switches.

ADDITIVE. Creates the empty ``feature_switch`` table; with no rows every switch
keeps its ``.env`` value, so an upgrade changes nothing. The ``CHECK`` list is a
literal copy of ``FEATURE_KEYS``: migrations never import application code. The
downgrade drops the table; the rows are the only data in it.

Revision id 0072, chained after 0071.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The 20 ``Settings`` field names of the OPS-FLAGS design, in its order.
_ALLOWED = (
    "'waf_collection_enabled', 'waf_alerting_enabled', 'waf_outcome_collection_enabled', "
    "'waf_client_collection_enabled', 'ips_collection_enabled', 'ips_alerting_enabled', "
    "'rule_match_collection_enabled', 'rule_alerting_enabled', 'pw_collection_enabled', "
    "'pw_alerting_enabled', 'custom_rules_enabled', 'custom_rules_alerting_enabled', "
    "'identity_collection_enabled', 'identity_alerting_enabled', 'catchall_collection_enabled', "
    "'catchall_new_sender_alerts_enabled', 'geo_collection_enabled', "
    "'qradar_health_collection_enabled', 'explanation_enabled', "
    "'inventory_new_source_alerts_enabled'"
)


def upgrade() -> None:
    op.create_table(
        "feature_switch",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.CheckConstraint(f"key IN ({_ALLOWED})", name=op.f("ck_feature_switch_key_allowed")),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_feature_switch")),
    )


def downgrade() -> None:
    op.drop_table("feature_switch")
