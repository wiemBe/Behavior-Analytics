"""SOC-10b: the IDENTITY_COLLECTION Ariel caller and its 15-minute cadence seed.

ADDITIVE. The identity collector records its Ariel searches under
``IDENTITY_COLLECTION``. The check grows by that one value, appended after
``ACCESS_CHECK`` so the order matches ``CALLERS``. The cadence row is inserted
with ``ON CONFLICT DO NOTHING`` at 900 seconds. This migration writes no audit
row.

The downgrade deletes the new caller's load rows before the older check is
restored, then deletes the cadence row.

Revision id 0069, chained after 0068.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_ariel_search_run_caller_known"
# The order matches the model's ``CALLERS`` so ``alembic check`` sees no drift.
_CALLER_NAMES = (
    "LOG_SOURCE_VOLUME",
    "ANOMALY_EXPLANATION",
    "RULE_MATCH",
    "WAF_COLLECTION",
    "IPS_COLLECTION",
    "PROPERTY_WATCH",
    "CATCHALL_SENDERS",
    "GEO_COLLECTION",
    "QRADAR_HEALTH",
    "HISTORY",
    "PREVIEW",
    "SCHEDULED_SEARCH",
    "MANUAL_RUN",
    "DOCTOR_AQL",
    "ACCESS_CHECK",
    "IDENTITY_COLLECTION",
)
_NEW_CALLERS = ", ".join(f"'{name}'" for name in _CALLER_NAMES)
_OLD_CALLERS = ", ".join(f"'{name}'" for name in _CALLER_NAMES[:-1])


def _set_callers(callers: str) -> None:
    # ``op.f``: the name is final, the naming convention must not prefix it again.
    op.drop_constraint(op.f(_CONSTRAINT), "ariel_search_run", type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), "ariel_search_run", f"caller IN ({callers})")


def upgrade() -> None:
    _set_callers(_NEW_CALLERS)
    op.execute(
        "INSERT INTO search_cadence (family, cadence_seconds, updated_at) "
        "VALUES ('IDENTITY_COLLECTION', 900, now()) ON CONFLICT (family) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ariel_search_run WHERE caller = 'IDENTITY_COLLECTION'")
    _set_callers(_OLD_CALLERS)
    op.execute("DELETE FROM search_cadence WHERE family = 'IDENTITY_COLLECTION'")
