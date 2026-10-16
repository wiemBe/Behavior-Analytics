"""Bounded retention for scheduled-search result aggregates.

`search_result_metric` rows whose `bucket_start` is older than
`SEARCH_RESULT_METRIC_RETENTION_DAYS` are deleted. The default is 90 days
(design F.8.2). Deletes run in bounded batches
(`SEARCH_RESULT_RETENTION_BATCH_SIZE` rows, at most
`SEARCH_RESULT_RETENTION_MAX_BATCHES` per run), each committed on its own, as
the catch-all and geo retention tasks do.

The table is a Timescale hypertable when the extension was present at
migration time. Chunk dropping (`drop_chunks` / `add_retention_policy`) is not
what those tasks do. It exists only as the optional policy in
`app.services.timescale.apply_policies`, gated by `RETENTION_ENABLED` and
`retention_search_result_metric_days` (default off). This task follows the
batch-delete pattern so it also runs when the table is a plain Postgres table
— the test schema, and a database migrated before Timescale was installed.
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
class SearchResultRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    #: Tables whose backlog was not cleared within the batch limit.
    incomplete: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete}


# Primary key is (execution_id, bucket_start, metric_key). The subquery picks
# the oldest rows and the DELETE removes exactly those, one batch at a time.
_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "search_result_metric",
        "DELETE FROM search_result_metric "
        "WHERE (execution_id, bucket_start, metric_key) IN ("
        "SELECT execution_id, bucket_start, metric_key FROM search_result_metric "
        "WHERE bucket_start < :cutoff LIMIT :batch)",
    ),
)


class SearchResultRetention:
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

    async def run(self) -> SearchResultRetentionReport:
        report = SearchResultRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.search_result_metric_retention_days)
        batch = self.settings.search_result_retention_batch_size
        for table, statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.search_result_retention_max_batches):
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
