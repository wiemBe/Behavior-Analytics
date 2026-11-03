"""ACC-3: the Ariel caller `ACCESS_CHECK` for the access check's probe search.

ADDITIVE. Each access check (every 6 hours and per "Check now") sends one Ariel search, the
stored probe. It is recorded in `ariel_search_run` under its own caller so every search we send
is accounted for in the SIEM load page. This widens the `caller_known` check by that one value.

The downgrade deletes the load records of the new caller (the older check would refuse them)
and restores the older check.

Revision id 0054, chained after 0053, the `DOCTOR_AQL` caller.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_ariel_search_run_caller_known"
# The order matches the model's ``CALLERS`` so ``alembic check`` sees no drift.
_OLD_CALLERS = (
    "'LOG_SOURCE_VOLUME', 'ANOMALY_EXPLANATION', 'RULE_MATCH', 'WAF_COLLECTION', "
    "'IPS_COLLECTION', 'PROPERTY_WATCH', 'CATCHALL_SENDERS', 'GEO_COLLECTION', "
    "'QRADAR_HEALTH', 'HISTORY', 'PREVIEW', 'SCHEDULED_SEARCH', 'MANUAL_RUN', 'DOCTOR_AQL'"
)
_NEW_CALLERS = _OLD_CALLERS + ", 'ACCESS_CHECK'"


def _set_callers(callers: str) -> None:
    # ``op.f``: the name is final, the naming convention must not prefix it again.
    op.drop_constraint(op.f(_CONSTRAINT), "ariel_search_run", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), "ariel_search_run", f"caller IN ({callers})")


def upgrade() -> None:
    _set_callers(_NEW_CALLERS)


def downgrade() -> None:
    op.execute("DELETE FROM ariel_search_run WHERE caller = 'ACCESS_CHECK'")
    _set_callers(_OLD_CALLERS)
