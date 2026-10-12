"""Correct the detector type recorded in stored WAF anomaly evidence.

Data only; no schema change. Until `AnomalyEvidence` became generic over its
detector type, WAF_REQUEST_RATE_SPIKE evidence was built with the Phase A
VOLUME_SPIKE type, and `waf_anomaly.details` persisted that borrowed value as
`anomaly_type`. The typed `detector_type` column was always correct; this makes
the stored evidence agree with it, so provenance read from `details` does not
claim a log-source detector produced a WAF incident.

Idempotent: rows that already agree are untouched. Downgrade is a no-op, because
restoring a value known to be false serves no purpose.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE waf_anomaly "
        "SET details = jsonb_set(details, '{anomaly_type}', to_jsonb(detector_type)) "
        "WHERE details->>'anomaly_type' IS NOT NULL "
        "AND details->>'anomaly_type' <> detector_type"
    )


def downgrade() -> None:
    pass
