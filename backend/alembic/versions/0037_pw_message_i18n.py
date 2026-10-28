"""Store the message reference beside the property-watch sentences that are persisted.

ADDITIVE. Four nullable JSONB columns hold ``{"code", "params"}``, each beside the English text
column it describes:

* ``property_watch.last_status_reason_i18n`` -- the status sentence of a watch;
* ``pw_anomaly.explanation_i18n`` and ``pw_anomaly.suppression_reason_i18n`` -- the detector
  sentence of an incident (the claim of a novelty incident lives inside ``details``);
* ``pw_anomaly_transition.reason_i18n`` -- the reason of a lifecycle transition.

Existing rows stay NULL and keep rendering their stored English. Nothing is backfilled. The
gap descriptions inside ``property_watch.history_progress`` carry their code inside the JSONB
and need no column.

Written on 0035 (the head of the branch this was cut from); MSG-3's 0036 sits between them at
the merge, which changes only ``down_revision``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("property_watch", "last_status_reason_i18n"),
    ("pw_anomaly", "explanation_i18n"),
    ("pw_anomaly", "suppression_reason_i18n"),
    ("pw_anomaly_transition", "reason_i18n"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(
            table,
            sa.Column(column, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.drop_column(table, column)
