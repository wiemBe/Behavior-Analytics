"""Daily delete of Ariel load rows older than the retention window.

Deletes run in bounded batches, each committed on its own. The cutoff is
strict: a row submitted at the cutoff stays. Not gated by RETENTION_ENABLED.
The table is not a hypertable, so this task is the only thing that prunes it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

#: Rows removed per statement. Keeps one transaction short.
BATCH_SIZE = 5000
MAX_BATCHES = 20

TABLE = "ariel_search_run"

_DELETE = (
    "DELETE FROM ariel_search_run WHERE id IN ("
    "SELECT id FROM ariel_search_run "
    "WHERE submitted_at < :cutoff "
    "LIMIT :batch)"
)


@dataclass
class ArielSearchRunRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class ArielSearchRunRetention:
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

    async def run(self) -> ArielSearchRunRetentionReport:
        report = ArielSearchRunRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.ariel_search_run_retention_days)
        deleted = 0
        for _ in range(MAX_BATCHES):
            result = await self.session.execute(
                text(_DELETE).bindparams(cutoff=cutoff, batch=BATCH_SIZE)
            )
            await self.session.commit()
            count = int(getattr(result, "rowcount", 0) or 0)
            deleted += count
            if count < BATCH_SIZE:
                break
        else:
            report.incomplete.append(TABLE)
        report.deleted[TABLE] = deleted
        return report
