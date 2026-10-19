"""Reads and writes of the history-backfill decision and scope rows (HISTORY-BACKFILL §B.7).

Every method is one statement whatever the number of rows (AGENTS.md rule 9). The collector
(a family adapter, inside its tick) is the only writer of `history_backfill_scope`, through
`write_scopes`. The API and the CLI write only `history_backfill_decision`: the answer, a
later NO-to-YES upgrade, and the two requests ("Read history now", "Stop reading") that each
adapter applies at its next tick. No method here commits, audits or checks a permission: the
caller owns the transaction, and the route or command owns the audit.

There is deliberately no method that turns a YES into a NO (§B.5).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.history.lifecycle import MAX_REASON_LENGTH, SCOPE_COLUMNS, bounded_reason
from app.models.enums import HistoryDecisionAnswer
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope

__all__ = ["HistoryBackfillRepository", "bounded_reason"]

_SCOPE_KEY = ("instance_id", "family", "scope_key")
_YES = HistoryDecisionAnswer.YES.value
_NO = HistoryDecisionAnswer.NO.value


class HistoryBackfillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----------------------------------------------------------------- reads
    async def decisions(
        self, instance_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, HistoryBackfillDecision]:
        """The decision of each console that has one: one statement."""
        if not instance_ids:
            return {}
        rows = await self.session.scalars(
            select(HistoryBackfillDecision)
            .where(HistoryBackfillDecision.instance_id.in_(list(instance_ids)))
            .execution_options(populate_existing=True)
        )
        return {row.instance_id: row for row in rows}

    async def scopes(
        self,
        instance_id: uuid.UUID,
        family: str,
        scope_keys: Collection[str] | None = None,
    ) -> dict[str, HistoryBackfillScope]:
        """The family's scope rows of a console by scope key: one statement."""
        statement = (
            select(HistoryBackfillScope)
            .where(
                HistoryBackfillScope.instance_id == instance_id,
                HistoryBackfillScope.family == family,
            )
            .execution_options(populate_existing=True)
        )
        if scope_keys is not None:
            if not scope_keys:
                return {}
            statement = statement.where(HistoryBackfillScope.scope_key.in_(list(scope_keys)))
        return {row.scope_key: row for row in await self.session.scalars(statement)}

    # ---------------------------------------------------------------- writes
    async def write_scopes(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Insert or update scope rows on their key: one statement for any number of rows.

        Every row must hold every column (nothing defaults silently) and a `reason` of at
        most 255 characters; build one with `bounded_reason`. Rows are validated before
        anything is sent.
        """
        if not rows:
            return
        expected = set(SCOPE_COLUMNS)
        for index, row in enumerate(rows):
            keys = set(row)
            if keys != expected:
                raise ValueError(
                    f"scope row {index} differs from the columns: "
                    f"missing {sorted(expected - keys)}, unknown {sorted(keys - expected)}"
                )
            reason = row["reason"]
            if reason is not None and len(reason) > MAX_REASON_LENGTH:
                raise ValueError(f"scope row {index} reason is longer than {MAX_REASON_LENGTH}")
        insert = pg_insert(cast(Table, HistoryBackfillScope.__table__))
        statement = insert.on_conflict_do_update(
            index_elements=list(_SCOPE_KEY),
            set_={
                name: insert.excluded[name] for name in SCOPE_COLUMNS if name not in _SCOPE_KEY
            },
        )
        # A list of parameter dicts: executemany, one statement to the driver.
        await self.session.execute(statement, [dict(row) for row in rows])

    async def record_decision(
        self,
        instance_id: uuid.UUID,
        *,
        answer: str,
        days: int | None,
        source: str,
        decided_by: str,
        at: datetime,
    ) -> tuple[HistoryBackfillDecision, bool]:
        """Insert the console's decision unless one exists; return the stored row.

        `ON CONFLICT DO NOTHING`: a row that appears between the caller's read and this
        insert is never overwritten. The flag is True when this call inserted the row.
        """
        inserted = await self.session.scalar(
            pg_insert(HistoryBackfillDecision)
            .values(
                instance_id=instance_id,
                answer=answer,
                days=days,
                decided_at=at,
                decided_by=decided_by,
                source=source,
            )
            .on_conflict_do_nothing(index_elements=["instance_id"])
            .returning(HistoryBackfillDecision.instance_id)
        )
        stored = (await self.decisions([instance_id])).get(instance_id)
        if stored is None:  # pragma: no cover - deleted between the two statements
            raise RuntimeError("the decision vanished while it was being recorded")
        return stored, inserted is not None

    async def upgrade_to_yes(
        self, instance_id: uuid.UUID, *, days: int, by: str, at: datetime
    ) -> bool:
        """NO to YES. True when a row changed; a YES or a missing console changes nothing."""
        changed = await self.session.scalar(
            update(HistoryBackfillDecision)
            .where(
                HistoryBackfillDecision.instance_id == instance_id,
                HistoryBackfillDecision.answer == _NO,
            )
            .values(answer=_YES, days=days, updated_at=at, updated_by=by)
            .returning(HistoryBackfillDecision.instance_id)
        )
        return changed is not None

    async def request_start(self, instance_id: uuid.UUID, *, by: str, at: datetime) -> bool:
        """Record "Read history now" on a YES; a later request overwrites the earlier one."""
        changed = await self.session.scalar(
            update(HistoryBackfillDecision)
            .where(
                HistoryBackfillDecision.instance_id == instance_id,
                HistoryBackfillDecision.answer == _YES,
            )
            .values(start_requested_at=at, start_requested_by=by)
            .returning(HistoryBackfillDecision.instance_id)
        )
        return changed is not None

    async def request_stop(self, instance_id: uuid.UUID, *, by: str, at: datetime) -> bool:
        """Record "Stop reading" on a YES; a later request overwrites the earlier one."""
        changed = await self.session.scalar(
            update(HistoryBackfillDecision)
            .where(
                HistoryBackfillDecision.instance_id == instance_id,
                HistoryBackfillDecision.answer == _YES,
            )
            .values(stop_requested_at=at, stop_requested_by=by)
            .returning(HistoryBackfillDecision.instance_id)
        )
        return changed is not None
