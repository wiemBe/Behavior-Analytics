"""JDBC-1 S0: allow the two JDBC-1 activation switches in the feature key CHECK.

ADDITIVE. Replaces the literal ``feature_switch`` key allow-list with one that
also permits the two rollout switches. No row is created and no other table
changes, so an upgrade changes no behavior: an absent row still leaves the
``.env``/default (off) in force. Migrations never import application code, so
the allow-list is a literal copy of ``FEATURE_KEYS``.

The downgrade removes rows for keys it no longer allows before restoring the
original 20-key CHECK; without that cleanup a stored switch row would make the
narrower constraint unsatisfiable.

Revision id 0074, chained after 0073.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_feature_switch_key_allowed"

#: The 20 keys of revision 0072, unchanged.
_OLD_ALLOWED = (
    "'waf_collection_enabled', 'waf_alerting_enabled', 'waf_outcome_collection_enabled', "
    "'waf_client_collection_enabled', 'ips_collection_enabled', 'ips_alerting_enabled', "
    "'rule_match_collection_enabled', 'rule_alerting_enabled', 'pw_collection_enabled', "
    "'pw_alerting_enabled', 'custom_rules_enabled', 'custom_rules_alerting_enabled', "
    "'identity_collection_enabled', 'identity_alerting_enabled', 'catchall_collection_enabled', "
    "'catchall_new_sender_alerts_enabled', 'geo_collection_enabled', "
    "'qradar_health_collection_enabled', 'explanation_enabled', "
    "'inventory_new_source_alerts_enabled'"
)

#: The two new keys are appended in the same order as ``FEATURE_KEYS``.
_NEW_ALLOWED = _OLD_ALLOWED + ", 'jdbc_collection_enabled', 'qradar_source_pause_enabled'"


def upgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "feature_switch", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), "feature_switch", f"key IN ({_NEW_ALLOWED})")


def downgrade() -> None:
    op.execute(
        "DELETE FROM feature_switch "
        "WHERE key IN ('jdbc_collection_enabled', 'qradar_source_pause_enabled')"
    )
    op.drop_constraint(op.f(_CONSTRAINT), "feature_switch", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), "feature_switch", f"key IN ({_OLD_ALLOWED})")
