"""Bounded retention for rule-health and detection-coverage snapshots.

``rule_health_snapshot`` ages by ``evaluated_at``
(``RULE_HEALTH_SNAPSHOT_RETENTION_DAYS``). ``detection_coverage_snapshot`` ages
by ``captured_at`` (``DETECTION_COVERAGE_SNAPSHOT_RETENTION_DAYS``). The two
cutoffs are independent. There is no reference guard: a snapshot's foreign keys
point at the rule or the instance, so deleting a snapshot does not delete the
parent, and nothing else points at the snapshot row.

Not gated by ``RETENTION_ENABLED``. ``skipped_reason`` stays unset. One batch
size and one batch cap are shared; ``incomplete`` names only the table that
hit the cap. Each batch commits on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

_STEPS: tuple[tuple[str, str, str], ...] = (
    (
        "rule_health_snapshot",
        "rule_health_snapshot_retention_days",
        "DELETE FROM rule_health_snapshot WHERE (rule_id, evaluated_at) IN ("
        "SELECT rule_id, evaluated_at FROM rule_health_snapshot "
        "WHERE evaluated_at < :cutoff LIMIT :batch)",
    ),
    (
        "detection_coverage_snapshot",
        "detection_coverage_snapshot_retention_days",
        "DELETE FROM detection_coverage_snapshot "
        "WHERE (instance_id, technique_id, captured_at) IN ("
        "SELECT instance_id, technique_id, captured_at "
        "FROM detection_coverage_snapshot "
        "WHERE captured_at < :cutoff LIMIT :batch)",
    ),
)


@dataclass
class SnapshotRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class SnapshotRetention:
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

    async def run(self) -> SnapshotRetentionReport:
        report = SnapshotRetentionReport()
        now = self._clock()
        batch = self.settings.snapshot_retention_batch_size
        for table, days_attr, statement in _STEPS:
            cutoff = now - timedelta(days=getattr(self.settings, days_attr))
            deleted = 0
            for _ in range(self.settings.snapshot_retention_max_batches):
                result = await self.session.execute(
                    text(statement).bindparams(cutoff=cutoff, batch=batch)
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
