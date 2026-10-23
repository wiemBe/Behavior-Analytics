"""Reads and writes for `anomaly_evaluation_fault` (T-170 §3, option B).

The engine and the doctor own different questions: the engine asks which faults
are open (one bulk read per run) and records attempts; the doctor asks how many
buckets were given up. Neither needs a per-source lookup, so every method here
is one statement.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitoring import AnomalyEvaluationFault, AnomalyEvaluationWatermark


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

    async def evaluated_through(
        self, log_source_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """The evaluation watermark per source, one statement whatever the fleet.

        A source that has never been judged is absent, not mapped to a sentinel.
        """
        ids = list(log_source_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(
                AnomalyEvaluationWatermark.log_source_id,
                AnomalyEvaluationWatermark.evaluated_through,
            ).where(AnomalyEvaluationWatermark.log_source_id.in_(ids))
        )
        return dict(rows.all())

    async def evaluation_faults(
        self,
        log_source_ids: Iterable[uuid.UUID],
        *,
        since: datetime,
        until: datetime,
    ) -> dict[uuid.UUID, list[AnomalyEvaluationFault]]:
        """Window-relevant faults per source, one statement.

        Open faults are always included (they are the current state). Given-up
        rows are included when their `bucket_start` falls in `[since, until)`,
        because the per-source count is "buckets in this window have no
        verdict" (the chart's range). Resolved rows still join by
        `resolved_at` in that half-open interval. `summary_counts` keeps
        counting given-up rows by `given_up_at` (the doctor's FAIL rule).
        """
        ids = list(log_source_ids)
        if not ids:
            return {}
        open_fault = and_(
            AnomalyEvaluationFault.resolved_at.is_(None),
            AnomalyEvaluationFault.given_up_at.is_(None),
        )
        given_up_in_window = and_(
            AnomalyEvaluationFault.given_up_at.is_not(None),
            AnomalyEvaluationFault.bucket_start >= since,
            AnomalyEvaluationFault.bucket_start < until,
        )
        resolved_in_window = and_(
            AnomalyEvaluationFault.resolved_at.is_not(None),
            AnomalyEvaluationFault.resolved_at >= since,
            AnomalyEvaluationFault.resolved_at < until,
        )
        rows = await self.session.scalars(
            select(AnomalyEvaluationFault)
            .where(
                AnomalyEvaluationFault.log_source_id.in_(ids),
                or_(open_fault, given_up_in_window, resolved_in_window),
            )
            .order_by(
                AnomalyEvaluationFault.log_source_id,
                AnomalyEvaluationFault.bucket_start.desc(),
            )
        )
        by_source: dict[uuid.UUID, list[AnomalyEvaluationFault]] = {}
        for row in rows.all():
            by_source.setdefault(row.log_source_id, []).append(row)
        return by_source

    async def summary_counts(
        self, *, since: datetime, until: datetime
    ) -> tuple[int, int]:
        """`(sources_with_evaluation_faults, buckets_not_judged)` in one statement.

        Sources: an open (retrying) fault, or a given-up bucket in the window.
        Buckets: given-up rows whose `given_up_at` falls in `[since, until)` —
        the doctor's FAIL rule (when the engine gave up), not when the
        unjudged bucket started. The per-source window (`evaluation_faults`)
        counts those same rows by `bucket_start` instead.
        A measured zero is 0 — we counted, and nothing matched.
        """
        open_fault = and_(
            AnomalyEvaluationFault.resolved_at.is_(None),
            AnomalyEvaluationFault.given_up_at.is_(None),
        )
        given_up_in_window = and_(
            AnomalyEvaluationFault.given_up_at.is_not(None),
            AnomalyEvaluationFault.given_up_at >= since,
            AnomalyEvaluationFault.given_up_at < until,
        )
        row = (
            await self.session.execute(
                select(
                    func.count(AnomalyEvaluationFault.log_source_id.distinct()).filter(
                        or_(open_fault, given_up_in_window)
                    ),
                    func.count().filter(given_up_in_window),
                ).select_from(AnomalyEvaluationFault)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)
