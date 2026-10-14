"""Bounded retention for geography buckets (D-10).

  * `geo_country_bucket` -- rows whose bucket started more than `GEO_RETENTION_DAYS` ago.

Deletes run in bounded batches (`GEO_RETENTION_BATCH_SIZE` rows, at most
`GEO_RETENTION_MAX_BATCHES` per run), each committed on its own, as catch-all retention does.
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
class GeoRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    #: Tables whose backlog was not cleared within the batch limit.
    incomplete: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete}


_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "geo_country_bucket",
        "DELETE FROM geo_country_bucket WHERE (instance_id, bucket_start, direction, slot) IN ("
        "SELECT instance_id, bucket_start, direction, slot FROM geo_country_bucket "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
)


class GeoRetention:
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

    async def run(self) -> GeoRetentionReport:
        report = GeoRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.geo_retention_days)
        batch = self.settings.geo_retention_batch_size
        for table, statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.geo_retention_max_batches):
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
