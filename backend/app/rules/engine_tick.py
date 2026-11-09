"""SOC-6i: a rule engine failure must not roll back the collected windows.

`collect_rule_matches` collects and then judges in one transaction. When the
engine raised, that transaction rolled the windows back, so the next tick sent
the same Ariel searches again, every tick, for as long as the engine bug lasted.

Here the engine runs inside a SAVEPOINT that is rolled back alone. The windows
commit either way, and a `collection_watermark` row (collector
`rule_match_evaluation`) remembers how far the engine has judged:

* `watermark_at` is the judged-through time. The engine probes
  `collected_at >= watermark_at`, so a window a failed run missed is judged by
  the next run that succeeds, whatever tick wrote it.
* `consecutive_failures` and `last_error` (the exception class only) are what
  `doctor` reads in its `watermarks` and `errors_recent` checks.
* `collection_metadata` holds the failed attempts on the current batch and, once
  the limit is reached, how many batches were given up.

After `rule_match_max_window_attempts` failed runs the engine gives up on the
batch: the marker moves past it, the windows stay stored and unjudged, and the
row says so. The windows are never judged as healthy, and a permanent bug costs
one failed engine run per tick on our own database, never an Ariel search.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.monitoring import CollectionWatermark
from app.rules.engine import RuleEvaluationReport, RuleMatchEngine

logger = logging.getLogger("app.rules.engine_tick")

EVALUATION_COLLECTOR = "rule_match_evaluation"


class RuleEngineTick:
    """The engine step of one rule-match tick, for one instance."""

    def __init__(
        self,
        session: AsyncSession,
        engine: RuleMatchEngine,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.engine = engine
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._row_id: uuid.UUID | None = None

    async def baseline(self, instance_id: uuid.UUID, started: datetime) -> datetime:
        """The time the engine has judged through, creating the row at `started`.

        Called before collection, so a first-ever run judges from its own start,
        as the in-transaction design did. The row is locked until the tick ends,
        so two ticks never judge one batch twice.
        """
        await self.session.execute(
            pg_insert(CollectionWatermark)
            .values(
                instance_id=instance_id,
                collector=EVALUATION_COLLECTOR,
                watermark_at=started,
                intervals_collected=0,
                consecutive_failures=0,
                collection_metadata={},
            )
            .on_conflict_do_nothing(constraint="uq_collection_watermark_key")
        )
        row = (
            await self.session.execute(
                select(CollectionWatermark.id, CollectionWatermark.watermark_at)
                .where(
                    CollectionWatermark.instance_id == instance_id,
                    CollectionWatermark.collector == EVALUATION_COLLECTOR,
                )
                .with_for_update()
            )
        ).one()
        self._row_id = row[0]
        return row[1] if row[1] is not None else started

    async def judge(
        self, instance: Any, instance_name: str, baseline: datetime
    ) -> str | None:
        """Run the engine in a savepoint; return the exception class on failure.

        The engine expires every loaded object when it finishes, so the marker is
        written with UPDATE statements, never through a held ORM row.
        """
        assert self._row_id is not None, "baseline() runs first"
        ended = self._clock()
        report: RuleEvaluationReport | None = None
        error_class: str | None = None
        try:
            async with self.session.begin_nested():
                report = await self.engine.evaluate(instance, written_since=baseline)
        except Exception as exc:
            error_class = type(exc).__name__
        if error_class is None:
            if report is not None and report.skipped == "collection_disabled":
                return None  # nothing was judged, so nothing may be marked judged
            await self._succeeded(ended)
            return None
        await self._failed(instance_name, ended, error_class)
        return error_class

    async def _state(self) -> tuple[int, dict[str, Any]]:
        row = (
            await self.session.execute(
                select(
                    CollectionWatermark.consecutive_failures,
                    CollectionWatermark.collection_metadata,
                ).where(CollectionWatermark.id == self._row_id)
            )
        ).one()
        return int(row[0]), dict(row[1] or {})

    async def _succeeded(self, ended: datetime) -> None:
        _failures, meta = await self._state()
        meta["batch_attempts"] = 0
        await self.session.execute(
            update(CollectionWatermark)
            .where(CollectionWatermark.id == self._row_id)
            .values(
                watermark_at=ended,
                last_run_at=ended,
                consecutive_failures=0,
                last_error=None,
                intervals_collected=CollectionWatermark.intervals_collected + 1,
                collection_metadata=meta,
            )
        )

    async def _failed(self, instance_name: str, ended: datetime, error_class: str) -> None:
        failures, meta = await self._state()
        attempts = int(meta.get("batch_attempts", 0)) + 1
        limit = self.settings.rule_match_max_window_attempts
        values: dict[str, Any] = {
            "last_run_at": ended,
            "consecutive_failures": failures + 1,
            "last_error": error_class,
        }
        gave_up = attempts >= limit
        if gave_up:
            # The batch stays stored and unjudged; the marker moves past it so a
            # permanent bug does not make every later tick re-judge it too.
            values["watermark_at"] = ended
            meta["batch_attempts"] = 0
            meta["given_up_batches"] = int(meta.get("given_up_batches", 0)) + 1
            meta["last_given_up_at"] = ended.isoformat()
        else:
            meta["batch_attempts"] = attempts
        values["collection_metadata"] = meta
        await self.session.execute(
            update(CollectionWatermark)
            .where(CollectionWatermark.id == self._row_id)
            .values(**values)
        )
        # The exception class only: a message can carry a value.
        logger.warning(
            "rule engine gave up on a batch" if gave_up else "rule engine run failed",
            extra={
                "instance": instance_name,
                "error_class": error_class,
                "attempts": attempts,
            },
        )
