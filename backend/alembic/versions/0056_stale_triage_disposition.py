"""D-55: the system-only STALE triage disposition.

ADDITIVE. Only ``anomaly_triage.disposition_known`` changes: STALE is added to its
closed set so the hourly job can close an untouched NEW item without touching the
anomaly's detector lifecycle or its alert. No Phase A table or enum changes.

The downgrade refuses while a STALE row exists, because silently deleting an analyst
record would destroy the history this migration is meant to preserve. Once those rows
have been reviewed and removed by an operator, it restores the SOC-1a constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_anomaly_triage_disposition_known"
_OLD_DISPOSITIONS = (
    "'TRUE_POSITIVE', 'BENIGN_TRUE_POSITIVE', 'NOISE', 'COLLECTION_PROBLEM', "
    "'KNOWN_CHANGE'"
)
_NEW_DISPOSITIONS = _OLD_DISPOSITIONS + ", 'STALE'"


def _set_dispositions(dispositions: str) -> None:
    # ``op.f``: the name is final; the naming convention must not prefix it again.
    op.drop_constraint(op.f(_CONSTRAINT), "anomaly_triage", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "anomaly_triage",
        f"disposition IS NULL OR disposition IN ({dispositions})",
    )


def upgrade() -> None:
    _set_dispositions(_NEW_DISPOSITIONS)


def downgrade() -> None:
    stale = op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM anomaly_triage WHERE disposition = 'STALE')")
    )
    if stale:
        raise RuntimeError(
            "cannot downgrade 0056 while anomaly_triage contains STALE dispositions; "
            "review and remove those rows first"
        )
    _set_dispositions(_OLD_DISPOSITIONS)
