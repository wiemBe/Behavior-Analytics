"""Store the message reference beside the Phase A detector sentences and every transition reason.

ADDITIVE. Five nullable JSONB columns hold ``{"code", "params"}``, each beside the English text
column it describes:

* ``log_source_anomaly.explanation_i18n`` -- the detector sentence of a Phase A incident, written
  and overwritten together with ``explanation``;
* ``log_source_anomaly.suppression_reason_i18n`` -- the reason an incident was suppressed;
* ``anomaly_state_transition.reason_i18n``, ``waf_anomaly_transition.reason_i18n`` and
  ``ips_anomaly_transition.reason_i18n`` -- the lifecycle reason of a transition, one code for
  every engine because ``app/anomaly/lifecycle.py`` is shared. (The property-watch transition
  table got its column in 0037.)

Existing rows stay NULL and keep rendering their stored English. Nothing is backfilled.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("log_source_anomaly", "explanation_i18n"),
    ("log_source_anomaly", "suppression_reason_i18n"),
    ("anomaly_state_transition", "reason_i18n"),
    ("waf_anomaly_transition", "reason_i18n"),
    ("ips_anomaly_transition", "reason_i18n"),
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
