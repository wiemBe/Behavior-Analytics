"""AQL-1: the Ariel caller `DOCTOR_AQL` for `doctor --aql`.

ADDITIVE. `doctor --aql` sends each collection family's real query once. Its searches are
recorded in `ariel_search_run` under their own caller, so a doctor run never shows as family
load. This widens the `caller_known` check by that one value.

The downgrade deletes the load records of the new caller (the older check would refuse them)
and restores the older check.

Revision id 0053, chained after 0052, the ABANDONED outcome.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_ariel_search_run_caller_known"
# The order matches the model's ``CALLERS`` so ``alembic check`` sees no drift.
_OLD_CALLERS = (
    "'LOG_SOURCE_VOLUME', 'ANOMALY_EXPLANATION', 'RULE_MATCH', 'WAF_COLLECTION', "
    "'IPS_COLLECTION', 'PROPERTY_WATCH', 'CATCHALL_SENDERS', 'GEO_COLLECTION', "
    "'QRADAR_HEALTH', 'HISTORY', 'PREVIEW', 'SCHEDULED_SEARCH', 'MANUAL_RUN'"
)
_NEW_CALLERS = _OLD_CALLERS + ", 'DOCTOR_AQL'"


def _set_callers(callers: str) -> None:
    # ``op.f``: the name is final, the naming convention must not prefix it again.
    op.drop_constraint(op.f(_CONSTRAINT), "ariel_search_run", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), "ariel_search_run", f"caller IN ({callers})")


def upgrade() -> None:
    _set_callers(_NEW_CALLERS)


def downgrade() -> None:
    op.execute("DELETE FROM ariel_search_run WHERE caller = 'DOCTOR_AQL'")
    _set_callers(_OLD_CALLERS)
