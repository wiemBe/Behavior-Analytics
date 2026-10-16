"""Bounded retention for resolved custom-rule breaches (design §F.10).

Resolved breaches age out by ``resolved_at``. A live breach is never deleted,
and neither is a row a rule still points at with ``active_breach_id``. Rules
themselves are kept until an operator deletes them.

``search_result_metric`` retention is F-4 and is not this task. Design §F.15
lists the breach task under F-2; the F-1 task prompt includes §F.10, and that
is the copy this module follows.

Deletes run in bounded batches, each committed on its own, the same shape as
``WafRetention``. There is no separate retention switch: the task is the
bound, and an empty table costs one statement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

_DELETE = (
    "DELETE FROM custom_rule_breach WHERE id IN ("
    "SELECT b.id FROM custom_rule_breach b "
    "WHERE b.state = 'RESOLVED' "
    "AND b.resolved_at IS NOT NULL "
    "AND b.resolved_at < :cutoff "
    "AND NOT EXISTS ("
    "SELECT 1 FROM custom_rule r WHERE r.active_breach_id = b.id"
    ") LIMIT :batch)"
)


@dataclass
class CustomRuleRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class CustomRuleRetention:
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

    async def run(self) -> CustomRuleRetentionReport:
        report = CustomRuleRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.custom_rule_breach_retention_days)
        batch = self.settings.custom_rules_retention_batch_size
        deleted = 0
        table = "custom_rule_breach"
        for _ in range(self.settings.custom_rules_retention_max_batches):
            result = await self.session.execute(
                text(_DELETE).bindparams(cutoff=cutoff, batch=batch)
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


__all__ = ["CustomRuleRetention", "CustomRuleRetentionReport"]
