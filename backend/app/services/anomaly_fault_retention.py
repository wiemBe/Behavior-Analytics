"""Bounded retention for anomaly evaluation faults.

A fault row is the record of a bucket with no verdict, so it ages differently
from the bucket itself:

  * an **open** fault (`resolved_at` and `given_up_at` both NULL) is the source's
    current state and is never deleted;
  * a **resolved** fault is cleared by a later judged bucket, but the row is
    kept for `ANOMALY_FAULT_RETENTION_DAYS` so "the 17:10 bucket failed once and
    was judged on retry" stays answerable;
  * a **given-up** fault is not a temporary marker. It is the only record that
    the bucket was never judged, which is why it is kept for the full window
    instead of clearing itself.

Deletes run in bounded batches (`BATCH_SIZE` rows, at most `MAX_BATCHES` per
run), each committed on its own, so a large backlog is worked down across runs
instead of in one long transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

#: Rows removed per statement. A module constant rather than a setting: the
#: table grows by at most one row per source and bucket, so the bound only has
#: to keep one transaction short.
BATCH_SIZE = 5000
MAX_BATCHES = 20

TABLE = "anomaly_evaluation_fault"

#: `COALESCE(resolved_at, given_up_at) IS NOT NULL` restates in SQL what the
#: docstring says: a row with neither column set is open and never matches.
_DELETE = (
    "DELETE FROM anomaly_evaluation_fault WHERE id IN ("
    "SELECT id FROM anomaly_evaluation_fault "
    "WHERE COALESCE(resolved_at, given_up_at) IS NOT NULL "
    "AND COALESCE(resolved_at, given_up_at) < :cutoff "
    "LIMIT :batch)"
)


@dataclass
class AnomalyFaultRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    #: Tables whose backlog was not cleared within the batch limit.
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class AnomalyFaultRetention:
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

    async def run(self) -> AnomalyFaultRetentionReport:
        report = AnomalyFaultRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.anomaly_fault_retention_days)
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
