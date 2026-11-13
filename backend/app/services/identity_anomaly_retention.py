"""Bounded retention for closed identity incidents.

Terminal rows (RESOLVED, NORMAL, INSUFFICIENT_DATA) whose age is
``COALESCE(resolved_at, anomaly_end, detected_at)`` older than
``IDENTITY_ANOMALY_RETENTION_DAYS`` are deleted. Live states are kept.

A row is kept while ``identity_detector_state.active_anomaly_id`` or
``open_anomaly_id`` still names it, and while any alert lists the id in
``source_anomaly_ids``, whatever the alert status. Nothing deletes an alert,
and the alert detail links to the incident through that list, so the incident
is kept for the life of the alert. ``evidence_snapshot`` is the alert's own
copy. The series deleter does not skip a bucket an open incident covers: the
incident row and the alert snapshot are the copies that outlive the series.

``identity_anomaly_transition`` leaves by ``ON DELETE CASCADE``.

This task is not ``IdentityRetention`` and is not gated by
``IDENTITY_RETENTION_ENABLED`` or ``RETENTION_ENABLED``. The series lookback
skip must not freeze incident aging. ``skipped_reason`` stays unset. Each
batch commits on its own. The batch size and the batch cap are the series
retention settings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import AnomalyFamily, AnomalyState
from app.services.anomaly_triage_retention import delete_orphan_triage

TERMINAL_STATES = (
    AnomalyState.RESOLVED.value,
    AnomalyState.NORMAL.value,
    AnomalyState.INSUFFICIENT_DATA.value,
)

#: Literals, not an f-string. ``test_retention_beat`` pins ``TERMINAL_STATES``
#: to this IN list.
DELETE_SQL = (
    "DELETE FROM identity_anomaly WHERE id IN ("
    "SELECT a.id FROM identity_anomaly a "
    "WHERE a.state IN ('RESOLVED', 'NORMAL', 'INSUFFICIENT_DATA') "
    "AND COALESCE(a.resolved_at, a.anomaly_end, a.detected_at) < :cutoff "
    "AND NOT EXISTS (SELECT 1 FROM identity_detector_state s "
    "WHERE s.active_anomaly_id = a.id OR s.open_anomaly_id = a.id) "
    "AND NOT EXISTS (SELECT 1 FROM alert al "
    "WHERE jsonb_exists(al.source_anomaly_ids, a.id::text)) "
    "LIMIT :batch)"
)


@dataclass
class IdentityAnomalyRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class IdentityAnomalyRetention:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> IdentityAnomalyRetentionReport:
        report = IdentityAnomalyRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.identity_anomaly_retention_days)
        batch = self.settings.identity_retention_batch_size
        deleted = 0
        table = "identity_anomaly"
        for _ in range(self.settings.identity_retention_max_batches):
            result = await self.session.execute(
                text(DELETE_SQL).bindparams(cutoff=cutoff, batch=batch)
            )
            await self.session.commit()
            count = int(getattr(result, "rowcount", 0) or 0)
            deleted += count
            if count < batch:
                break
        else:
            report.incomplete.append(table)
        report.deleted[table] = deleted
        orphaned = await delete_orphan_triage(
            self.session, AnomalyFamily.IDENTITY, batch_size=batch
        )
        if orphaned:
            report.deleted["anomaly_triage"] = orphaned
        return report
