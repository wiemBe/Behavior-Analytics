"""Bounded retention for closed Phase A log-source incidents.

Terminal rows (RESOLVED, NORMAL, INSUFFICIENT_DATA) whose age is
``COALESCE(resolved_at, anomaly_end, detected_at)`` older than
``LOG_SOURCE_ANOMALY_RETENTION_DAYS`` are deleted. Live states — CANDIDATE,
OPEN, RECOVERING, SUPPRESSED — are never deleted, whatever their age.

A row is kept while ``log_source_detector_state.active_anomaly_id`` or
``open_anomaly_id`` still names it (any detector state, not only OPEN), and
while any alert lists the id in ``source_anomaly_ids``, whatever the alert
status. Nothing deletes an alert, and the alert detail links to the incident
through that list, so the incident is kept for the life of the alert.
``evidence_snapshot`` is the alert's own copy and is not a guard.
``source_id`` is the log source, not the incident.

Children that exist only for the incident leave in the same delete, by
``ON DELETE CASCADE``: ``anomaly_state_transition``, ``anomaly_explanation``,
and through the explanation ``anomaly_explanation_dimension`` and
``anomaly_explanation_contributor``. Those are not a skip.

The task is not gated by ``RETENTION_ENABLED``. ``skipped_reason`` stays unset.
Deletes run in bounded batches, each committed on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import AnomalyState

TERMINAL_STATES = (
    AnomalyState.RESOLVED.value,
    AnomalyState.NORMAL.value,
    AnomalyState.INSUFFICIENT_DATA.value,
)

#: Literals, not an f-string: the states are fixed enum values. ``test_retention_beat``
#: pins ``TERMINAL_STATES`` to this IN list. ``SUPPRESSED`` is live and is not in it.
DELETE_SQL = (
    "DELETE FROM log_source_anomaly WHERE id IN ("
    "SELECT a.id FROM log_source_anomaly a "
    "WHERE a.state IN ('RESOLVED', 'NORMAL', 'INSUFFICIENT_DATA') "
    "AND COALESCE(a.resolved_at, a.anomaly_end, a.detected_at) < :cutoff "
    "AND NOT EXISTS (SELECT 1 FROM log_source_detector_state s "
    "WHERE s.active_anomaly_id = a.id OR s.open_anomaly_id = a.id) "
    "AND NOT EXISTS (SELECT 1 FROM alert al "
    "WHERE jsonb_exists(al.source_anomaly_ids, a.id::text)) "
    "LIMIT :batch)"
)


@dataclass
class LogSourceAnomalyRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class LogSourceAnomalyRetention:
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

    async def run(self) -> LogSourceAnomalyRetentionReport:
        report = LogSourceAnomalyRetentionReport()
        cutoff = self._clock() - timedelta(
            days=self.settings.log_source_anomaly_retention_days
        )
        batch = self.settings.log_source_anomaly_retention_batch_size
        deleted = 0
        table = "log_source_anomaly"
        for _ in range(self.settings.log_source_anomaly_retention_max_batches):
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
        return report
