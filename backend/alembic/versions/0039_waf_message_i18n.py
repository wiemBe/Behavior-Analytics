"""Store the message reference beside the WAF anomaly sentences that are persisted.

ADDITIVE. Two nullable JSONB columns hold ``{"code", "params"}``, each beside the English text
column it describes:

* ``waf_anomaly.explanation_i18n`` -- the detector sentence of an incident;
* ``waf_anomaly.suppression_reason_i18n`` -- why an incident was suppressed (a maintenance
  window).

The caveat and the lower-bound wrap live inside ``waf_anomaly.details`` and need no column.
Existing rows stay NULL and keep rendering their stored English. Nothing is backfilled.

Written on 0037 (the head of the branch this was cut from); MSG-5's 0038 sits between them at
the merge, which changes only ``down_revision``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0039"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("waf_anomaly", "explanation_i18n"),
    ("waf_anomaly", "suppression_reason_i18n"),
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
