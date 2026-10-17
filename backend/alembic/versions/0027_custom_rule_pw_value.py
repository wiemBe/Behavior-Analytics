"""Phase G, the F.1 extension: a custom rule may pin a property-watch value.

ADDITIVE. Revision 0027 revises 0026. Adds `custom_rule.pw_value_id` (FK to
`pw_value.id`, ON DELETE RESTRICT, indexed) and drops and recreates the subject
pairing CHECK so `PW_VALUE` is a legal kind (design §G.10.2, §G.7.4). `0024` and
`0026` are not edited. RESTRICT means deleting a watch whose value a rule pins is
refused at the database as well as by the API.

Downgrade deletes the `PW_VALUE` rules (their breaches cascade) before the old
CHECK returns: those rows could not satisfy it, and a rule with no subject is
not a rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_custom_rule_subject_matches_kind"

#: The 0024 pairing, verbatim: what the downgrade restores.
_OLD_CHECK = (
    "(subject_kind = 'LOG_SOURCE' AND log_source_id IS NOT NULL "
    "AND source_class IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL) OR "
    "(subject_kind = 'FLEET_CLASS' AND source_class IS NOT NULL "
    "AND log_source_id IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL) OR "
    "(subject_kind = 'WAF_ENDPOINT' AND waf_endpoint_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND ips_entity_id IS NULL) OR "
    "(subject_kind = 'IPS_ENTITY' AND ips_entity_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND waf_endpoint_id IS NULL) OR "
    "(subject_kind = 'IPS_SOURCE' AND log_source_id IS NOT NULL "
    "AND source_class IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL)"
)

_NEW_CHECK = (
    "(subject_kind = 'LOG_SOURCE' AND log_source_id IS NOT NULL "
    "AND source_class IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL "
    "AND pw_value_id IS NULL) OR "
    "(subject_kind = 'FLEET_CLASS' AND source_class IS NOT NULL "
    "AND log_source_id IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL "
    "AND pw_value_id IS NULL) OR "
    "(subject_kind = 'WAF_ENDPOINT' AND waf_endpoint_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND ips_entity_id IS NULL "
    "AND pw_value_id IS NULL) OR "
    "(subject_kind = 'IPS_ENTITY' AND ips_entity_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND waf_endpoint_id IS NULL "
    "AND pw_value_id IS NULL) OR "
    "(subject_kind = 'IPS_SOURCE' AND log_source_id IS NOT NULL "
    "AND source_class IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL "
    "AND pw_value_id IS NULL) OR "
    "(subject_kind = 'PW_VALUE' AND pw_value_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND waf_endpoint_id IS NULL "
    "AND ips_entity_id IS NULL)"
)


def upgrade() -> None:
    op.add_column("custom_rule", sa.Column("pw_value_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_custom_rule_pw_value_id_pw_value"),
        "custom_rule",
        "pw_value",
        ["pw_value_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_custom_rule_pw_value_id"), "custom_rule", ["pw_value_id"])
    op.drop_constraint(op.f(_CHECK_NAME), "custom_rule", type_="check")
    op.create_check_constraint(op.f(_CHECK_NAME), "custom_rule", _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(op.f(_CHECK_NAME), "custom_rule", type_="check")
    # Breaches cascade with their rule. Nothing else references a rule by FK.
    op.execute("DELETE FROM custom_rule WHERE subject_kind = 'PW_VALUE'")
    op.execute("DELETE FROM custom_rule_watermark WHERE subject_kind = 'PW_VALUE'")
    op.create_check_constraint(op.f(_CHECK_NAME), "custom_rule", _OLD_CHECK)
    op.drop_index(op.f("ix_custom_rule_pw_value_id"), table_name="custom_rule")
    op.drop_constraint(
        op.f("fk_custom_rule_pw_value_id_pw_value"), "custom_rule", type_="foreignkey"
    )
    op.drop_column("custom_rule", "pw_value_id")
