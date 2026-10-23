"""Bounded retention for IPS collection buckets, metrics and retired entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings


@dataclass
class IpsRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


_STATEMENTS = (
    (
        "ips_entity_metric",
        "DELETE FROM ips_entity_metric WHERE (ips_entity_id, bucket_start) IN ("
        "SELECT ips_entity_id, bucket_start FROM ips_entity_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
    (
        "ips_source_bucket",
        "DELETE FROM ips_source_bucket WHERE (log_source_id, bucket_start) IN ("
        "SELECT log_source_id, bucket_start FROM ips_source_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
    (
        "ips_entity",
        "DELETE FROM ips_entity WHERE id IN (SELECT e.id FROM ips_entity e "
        "WHERE e.active = false AND e.retired_at IS NOT NULL AND e.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM ips_entity_metric m WHERE m.ips_entity_id = e.id) "
        "LIMIT :batch)",
    ),
)


class IpsRetention:
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

    async def run(self) -> IpsRetentionReport:
        report = IpsRetentionReport()
        if not self.settings.ips_retention_enabled:
            report.skipped_reason = "IPS retention is disabled"
            return report
        if self.settings.ips_metric_retention_days <= self.settings.ips_baseline_lookback_days:
            # Startup refuses this only when IPS collection and retention are
            # both on, so a collection-off deploy can still reach a run.
            # Deleting would drop samples a baseline rebuild relies on.
            report.skipped_reason = (
                "IPS_METRIC_RETENTION_DAYS does not exceed IPS_BASELINE_LOOKBACK_DAYS; "
                "nothing deleted"
            )
            return report
        cutoff = self._clock() - timedelta(days=self.settings.ips_metric_retention_days)
        for table, statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.ips_retention_max_batches):
                result = await self.session.execute(
                    text(statement).bindparams(
                        cutoff=cutoff, batch=self.settings.ips_retention_batch_size
                    )
                )
                await self.session.commit()
                count = int(getattr(result, "rowcount", 0) or 0)
                deleted += count
                if count < self.settings.ips_retention_batch_size:
                    break
            else:
                report.incomplete.append(table)
            report.deleted[table] = deleted
        return report
