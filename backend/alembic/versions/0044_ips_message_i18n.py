"""Store the message reference beside the IPS anomaly sentences that are persisted.

ADDITIVE. Two nullable JSONB columns hold ``{"code", "params"}``, each beside the English text
column it describes:

* ``ips_anomaly.explanation_i18n`` -- the detector sentence of an incident;
* ``ips_anomaly.suppression_reason_i18n`` -- why an incident was suppressed (a maintenance
  window).

The caveat and the lower-bound wrap live inside ``ips_anomaly.details`` and need no column.
Existing rows stay NULL and keep rendering their stored English. Nothing is backfilled.

Written as 0043 on 0042; renumbered 0044 at the merge, after MSG-8a's 0043, so the revision ids
rise along the chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ips_anomaly", "explanation_i18n"),
    ("ips_anomaly", "suppression_reason_i18n"),
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
