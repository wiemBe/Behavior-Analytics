"""Bounded retention for measured rule-match aggregates.

`rule_match_metric` (Timescale hypertable on `bucket_start`) and
`rule_match_window` (plain, `(instance_id, window_start)`) were created
by migration `0012` with no retention and grew without bound. This
deleter gives them the bounded age every other measured table already
has through a deleter or a Timescale policy.

Not gated by `RETENTION_ENABLED`. One batch size and one batch cap are
shared; `incomplete` names only the table that hit the cap. Each batch
commits on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "rule_match_metric",
        "DELETE FROM rule_match_metric "
        "WHERE (rule_id, bucket_start) IN ("
        "SELECT rule_id, bucket_start FROM rule_match_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
    (
        "rule_match_window",
        "DELETE FROM rule_match_window "
        "WHERE (instance_id, window_start) IN ("
        "SELECT instance_id, window_start FROM rule_match_window "
        "WHERE window_start < :cutoff LIMIT :batch)",
    ),
)


@dataclass
class RuleMatchRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class RuleMatchRetention:
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

    async def run(self) -> RuleMatchRetentionReport:
        report = RuleMatchRetentionReport()
        now = self._clock()
        batch = self.settings.rule_match_retention_batch_size
        for table, statement in _STATEMENTS:
            cutoff = now - timedelta(days=self.settings.rule_match_retention_days)
            deleted = 0
            for _ in range(self.settings.rule_match_retention_max_batches):
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
