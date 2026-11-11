"""Bounded retention for immutable shift handover records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings


@dataclass
class HandoverRetentionReport:
    deleted: int
    incomplete: bool

    def as_dict(self) -> dict[str, int | bool]:
        return {"deleted": self.deleted, "incomplete": self.incomplete}


class HandoverRetention:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def run(self, *, now: datetime | None = None) -> HandoverRetentionReport:
        cutoff = (now or datetime.now(UTC)) - timedelta(
            days=self.settings.handover_retention_days
        )
        deleted = 0
        for _ in range(self.settings.handover_retention_max_batches):
            result = await self.session.execute(
                text(
                    "DELETE FROM shift_handover WHERE id IN ("
                    "SELECT id FROM shift_handover WHERE written_at < :cutoff "
                    "ORDER BY written_at LIMIT :batch)"
                ).bindparams(cutoff=cutoff, batch=self.settings.handover_retention_batch_size)
            )
            await self.session.commit()
            count = int(getattr(result, "rowcount", 0) or 0)
            deleted += count
            if count < self.settings.handover_retention_batch_size:
                return HandoverRetentionReport(deleted=deleted, incomplete=False)
        return HandoverRetentionReport(deleted=deleted, incomplete=True)
