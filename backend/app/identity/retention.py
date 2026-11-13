"""Delete identity buckets older than the count retention window.

One batch picks the old ``bucket_start`` values from all three tables, deletes
the usernames, the counts and the bucket for those starts, then commits.
``bucket_start`` equal to the cutoff stays. Profiles are not deleted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.identity_detection import IdentityBucket, IdentityEventCount, IdentityTopUsername

_OLD_STARTS = text(
    "SELECT bucket_start FROM ("
    "SELECT bucket_start FROM identity_bucket WHERE bucket_start < :cutoff "
    "UNION SELECT bucket_start FROM identity_event_count WHERE bucket_start < :cutoff "
    "UNION SELECT bucket_start FROM identity_top_username WHERE bucket_start < :cutoff"
    ") AS old_buckets ORDER BY bucket_start LIMIT :batch"
)
_TABLES = (
    ("identity_top_username", IdentityTopUsername),
    ("identity_event_count", IdentityEventCount),
    ("identity_bucket", IdentityBucket),
)


@dataclass
class IdentityRetentionReport:
    deleted: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "deleted": self.deleted,
            "incomplete": self.incomplete,
            "skipped_reason": self.skipped_reason,
        }


class IdentityRetention:
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

    async def run(self) -> IdentityRetentionReport:
        report = IdentityRetentionReport(deleted={name: 0 for name, _model in _TABLES})
        if not self.settings.identity_retention_enabled:
            report.skipped_reason = "identity retention is disabled"
            return report
        cutoff = self._clock() - timedelta(days=self.settings.identity_count_retention_days)
        batch = self.settings.identity_retention_batch_size
        for _ in range(self.settings.identity_retention_max_batches):
            starts = list(
                (
                    await self.session.scalars(
                        _OLD_STARTS.bindparams(cutoff=cutoff, batch=batch)
                    )
                ).all()
            )
            if not starts:
                break
            for name, model in _TABLES:
                result = await self.session.execute(
                    delete(model).where(model.bucket_start.in_(starts))
                )
                report.deleted[name] += int(getattr(result, "rowcount", 0) or 0)
            await self.session.commit()
            if len(starts) < batch:
                break
        else:
            report.incomplete.append("identity_bucket")
        return report
