"""Reads and writes for `anomaly_evaluation_fault` (T-170 §3, option B).

The engine and the doctor own different questions: the engine asks which faults
are open (one bulk read per run) and records attempts; the doctor asks how many
buckets were given up. Neither needs a per-source lookup, so every method here
is one statement.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitoring import AnomalyEvaluationFault


class AnomalyFaultRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def open_source_ids(self) -> set[uuid.UUID]:
        """Sources with a fault awaiting a retry, one statement whatever the fleet.

        Called once per engine instance (T-170 §3 "SQL cost"). The set holds the
        recovery decision, so the success path issues no update for a source
        that has no open fault, and the whole table is state, not history: an
        open fault exists only for a source that is failing now.
        """
        rows = await self.session.scalars(
            select(AnomalyEvaluationFault.log_source_id).where(
                AnomalyEvaluationFault.resolved_at.is_(None),
                AnomalyEvaluationFault.given_up_at.is_(None),
            )
        )
        return set(rows.all())

    async def record_failure(
        self,
        log_source_id: uuid.UUID,
        bucket_start: datetime,
        *,
        error_class: str,
        at: datetime,
    ) -> int:
        """Count one failed attempt on (source, bucket); return the new count.

        A single upsert. `first_failed_at` keeps the first attempt's time and
        the row is never reopened after it closes: the engine advances its
        watermark past a given-up bucket, so that bucket is not attempted again.
        """
        statement = (
            pg_insert(AnomalyEvaluationFault)
            .values(
                log_source_id=log_source_id,
                bucket_start=bucket_start,
                attempts=1,
                first_failed_at=at,
                last_failed_at=at,
                error_class=error_class,
            )
            .on_conflict_do_update(
                index_elements=["log_source_id", "bucket_start"],
                set_={
                    "attempts": AnomalyEvaluationFault.attempts + 1,
                    "last_failed_at": at,
                    "error_class": error_class,
                },
            )
            .returning(AnomalyEvaluationFault.attempts)
        )
        attempts = await self.session.scalar(statement)
        if attempts is None:
            # The upsert always returns the row it wrote; no row means the
            # statement did not run as written, and guessing a count would make
            # the give-up bound meaningless.
            raise RuntimeError("the fault upsert returned no attempt count")
        return int(attempts)

    async def give_up(
        self, log_source_id: uuid.UUID, bucket_start: datetime, *, at: datetime
    ) -> None:
        """Close the bucket as "not judged" so the watermark can move past it."""
        await self.session.execute(
            update(AnomalyEvaluationFault)
            .where(
                AnomalyEvaluationFault.log_source_id == log_source_id,
                AnomalyEvaluationFault.bucket_start == bucket_start,
            )
            .values(given_up_at=at)
        )

    async def resolve(self, log_source_id: uuid.UUID, *, at: datetime) -> int:
        """Close the source's open fault; return how many rows it closed."""
        result = await self.session.execute(
            update(AnomalyEvaluationFault)
            .where(
                AnomalyEvaluationFault.log_source_id == log_source_id,
                AnomalyEvaluationFault.resolved_at.is_(None),
                AnomalyEvaluationFault.given_up_at.is_(None),
            )
            .values(resolved_at=at)
        )
        return int(getattr(result, "rowcount", 0) or 0)
