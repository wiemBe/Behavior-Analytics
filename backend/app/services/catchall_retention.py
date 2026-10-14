"""Bounded retention for catch-all sender telemetry (Phase C.2b).

  * `catchall_bucket` -- rows whose bucket started more than
    `CATCHALL_RETENTION_DAYS` ago.
  * `catchall_sender` -- senders retired (not seen for `CATCHALL_SENDER_ACTIVE_DAYS`)
    more than `CATCHALL_RETENTION_DAYS` ago. An active sender, and one still in
    the review queue, is never deleted whatever its age.

Deletes run in bounded batches (`CATCHALL_RETENTION_BATCH_SIZE` rows, at most
`CATCHALL_RETENTION_MAX_BATCHES` per table per run), each committed on its own,
as WAF retention does.
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
class CatchallRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    #: Tables whose backlog was not cleared within the batch limit.
    incomplete: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete}


_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "catchall_bucket",
        "DELETE FROM catchall_bucket WHERE (catchall_log_source_id, bucket_start) IN ("
        "SELECT catchall_log_source_id, bucket_start FROM catchall_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
    (
        "catchall_sender",
        "DELETE FROM catchall_sender WHERE id IN ("
        "SELECT id FROM catchall_sender "
        "WHERE active = false AND retired_at IS NOT NULL AND retired_at < :cutoff "
        "LIMIT :batch)",
    ),
)


class CatchallRetention:
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

    async def run(self) -> CatchallRetentionReport:
        report = CatchallRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.catchall_retention_days)
        batch = self.settings.catchall_retention_batch_size
        for table, statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.catchall_retention_max_batches):
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
