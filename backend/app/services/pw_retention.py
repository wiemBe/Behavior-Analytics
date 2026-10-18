"""Bounded retention for property-watch buckets, metrics and retired values (design §G.7.2).

Bucket and metric rows go after `pw_metric_retention_days`. A value row goes when it is
retired, its retirement is older than that, and it has no metric left: the metrics are
deleted first, so in one run a value can leave right after its last metric aged out, and
never before. Every statement deletes at most `pw_retention_batch_size` rows
per batch and at most `pw_retention_max_batches` batches per run; a table that still has
more is reported `incomplete` and continues the next day.

Deleting a watch deletes its values by cascade; this task only ages what remains. It runs
whether or not collection is enabled: switching collection off must not freeze value text
in the database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings


@dataclass
class PwRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete}


_STATEMENTS = (
    (
        "pw_value_metric",
        "DELETE FROM pw_value_metric WHERE (pw_value_id, bucket_start) IN ("
        "SELECT pw_value_id, bucket_start FROM pw_value_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_value_metric WHERE bucket_start < :cutoff)",
    ),
    (
        "pw_bucket",
        "DELETE FROM pw_bucket WHERE (watch_id, bucket_start) IN ("
        "SELECT watch_id, bucket_start FROM pw_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_bucket WHERE bucket_start < :cutoff)",
    ),
    (
        "pw_value",
        "DELETE FROM pw_value WHERE id IN (SELECT v.id FROM pw_value v "
        "WHERE v.active = false AND v.retired_at IS NOT NULL AND v.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_value_metric m WHERE m.pw_value_id = v.id) "
        "LIMIT :batch)",
        "SELECT EXISTS (SELECT 1 FROM pw_value v "
        "WHERE v.active = false AND v.retired_at IS NOT NULL AND v.retired_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM pw_value_metric m WHERE m.pw_value_id = v.id))",
    ),
)


class PwRetention:
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

    async def run(self) -> PwRetentionReport:
        report = PwRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.pw_metric_retention_days)
        batch = self.settings.pw_retention_batch_size
        for table, statement, remaining_statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.pw_retention_max_batches):
                result = await self.session.execute(
                    text(statement).bindparams(cutoff=cutoff, batch=batch)
                )
                await self.session.commit()
                count = int(getattr(result, "rowcount", 0) or 0)
                deleted += count
                if count < batch:
                    break
            else:
                remaining = await self.session.scalar(
                    text(remaining_statement).bindparams(cutoff=cutoff)
                )
                if remaining:
                    report.incomplete.append(table)
            report.deleted[table] = deleted
        return report
