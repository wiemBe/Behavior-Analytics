"""Bounded retention for rule-health and detection-coverage snapshots.

``rule_health_snapshot`` ages by ``evaluated_at``
(``RULE_HEALTH_SNAPSHOT_RETENTION_DAYS``). ``detection_coverage_snapshot`` ages
by ``captured_at`` (``DETECTION_COVERAGE_SNAPSHOT_RETENTION_DAYS``). The two
cutoffs are independent. There is no reference guard: a snapshot's foreign keys
point at the rule or the instance, so deleting a snapshot does not delete the
parent, and nothing else points at the snapshot row.

The entity-timeline mentions of offenses (``entity_mention``, source OFFENSE) belong to the
offense, whose snapshots a Timescale policy drops outside this code
(``RETENTION_OFFENSE_SNAPSHOT_DAYS``). When that policy is on, each run deletes the mentions of
offenses that have no snapshot left, among mentions older than the policy's cutoff, in bounded
batches. An offense that still has a snapshot keeps its mentions.

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


#: Mentions of offenses with no snapshot left. Every OFFENSE mention's `ref_id` is the QRadar
#: offense id as text.
_OFFENSE_MENTION_SQL = (
    "DELETE FROM entity_mention WHERE (instance_id, kind, value_norm, source, ref_table, "
    "ref_id, occurred_at) IN (SELECT m.instance_id, m.kind, m.value_norm, m.source, "
    "m.ref_table, m.ref_id, m.occurred_at FROM entity_mention m "
    "WHERE m.source = 'OFFENSE' AND m.occurred_at < :cutoff "
    "AND NOT EXISTS (SELECT 1 FROM offense_snapshot s "
    "WHERE s.instance_id = m.instance_id AND s.qradar_offense_id = m.ref_id::bigint) "
    "LIMIT :batch)"
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
        await self._offense_mentions(report, now, batch)
        return report

    async def _offense_mentions(
        self, report: SnapshotRetentionReport, now: datetime, batch: int
    ) -> None:
        days = self.settings.retention_offense_snapshot_days
        if not self.settings.retention_enabled or days is None:
            return
        cutoff = now - timedelta(days=days)
        deleted = 0
        for _ in range(self.settings.snapshot_retention_max_batches):
            result = await self.session.execute(
                text(_OFFENSE_MENTION_SQL).bindparams(cutoff=cutoff, batch=batch)
            )
            await self.session.commit()
            count = int(getattr(result, "rowcount", 0) or 0)
            deleted += count
            if count < batch:
                break
        else:
            report.incomplete.append("entity_mention_offense")
        if deleted:
            report.deleted["entity_mention_offense"] = deleted
