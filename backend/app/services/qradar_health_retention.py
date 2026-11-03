"""Bounded retention for the stored QRadar health windows (D-42).

  * `qradar_health_run`, `qradar_health_metric_bucket`, `qradar_health_notice_bucket` and
    `qradar_health_rest_sample` -- rows whose window started more than
    `QRADAR_HEALTH_RETENTION_DAYS` ago.

Deletes run in bounded batches (`QRADAR_HEALTH_RETENTION_BATCH_SIZE` rows, at most
`QRADAR_HEALTH_RETENTION_MAX_BATCHES` per table per run), each committed on its own, as the
geography retention does.
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
class QRadarHealthRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    #: Tables whose backlog was not cleared within the batch limit.
    incomplete: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "incomplete": self.incomplete}


def _statement(table: str, key: str, time_column: str) -> tuple[str, str]:
    return (
        table,
        f"DELETE FROM {table} WHERE ({key}) IN ("  # noqa: S608 - fixed identifiers
        f"SELECT {key} FROM {table} WHERE {time_column} < :cutoff LIMIT :batch)",
    )


_STATEMENTS: tuple[tuple[str, str], ...] = (
    _statement("qradar_health_run", "instance_id, window_start", "window_start"),
    _statement(
        "qradar_health_metric_bucket",
        "instance_id, bucket_start, host, metric, element",
        "bucket_start",
    ),
    _statement(
        "qradar_health_notice_bucket",
        "instance_id, bucket_start, kind, host, qid_name",
        "bucket_start",
    ),
    _statement("qradar_health_rest_sample", "instance_id, slot_start", "slot_start"),
)


class QRadarHealthRetention:
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

    async def run(self) -> QRadarHealthRetentionReport:
        report = QRadarHealthRetentionReport()
        cutoff = self._clock() - timedelta(days=self.settings.qradar_health_retention_days)
        batch = self.settings.qradar_health_retention_batch_size
        for table, statement in _STATEMENTS:
            deleted = 0
            for _ in range(self.settings.qradar_health_retention_max_batches):
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
