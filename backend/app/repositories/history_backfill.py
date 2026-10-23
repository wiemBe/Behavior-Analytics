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

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes
from sqlalchemy.orm.util import identity_key

from app.history.lifecycle import MAX_REASON_LENGTH, SCOPE_COLUMNS, bounded_reason
from app.models.enums import HistoryDecisionAnswer
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope

__all__ = ["HistoryBackfillRepository", "bounded_reason"]

_SCOPE_KEY = ("instance_id", "family", "scope_key")
_YES = HistoryDecisionAnswer.YES.value
_NO = HistoryDecisionAnswer.NO.value
_MAX_ERROR_CLASS_LENGTH = 64


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
        table = cast(Table, HistoryBackfillScope.__table__)
        insert = pg_insert(table)
        statement = insert.on_conflict_do_update(
            index_elements=list(_SCOPE_KEY),
            set_={
                name: insert.excluded[name] for name in SCOPE_COLUMNS if name not in _SCOPE_KEY
            },
        )
        loaded = {}
        for row in rows:
            key = (row["instance_id"], row["family"], row["scope_key"])
            found = self.session.identity_map.get(identity_key(HistoryBackfillScope, key))
            if found is not None:
                loaded[key] = found
        # A list of parameter dicts: executemany, one statement to the driver.
        if not loaded:
            await self.session.execute(statement, [dict(row) for row in rows])
            return
        # The bulk upsert does not touch an ORM row already loaded in the session, and a
        # caller that reads that row afterwards would act on the state before the write.
        # RETURNING hands back the written values in the same statement, so the loaded
        # rows are synchronised without a SELECT: still one statement, whatever the count.
        # (The ORM entity form of this upsert runs more than one statement for many rows.)
        result = await self.session.execute(
            statement.returning(*table.c), [dict(row) for row in rows]
        )
        for written in result.mappings():
            found = loaded.get((written["instance_id"], written["family"], written["scope_key"]))
            if found is None:
                continue
            for name in SCOPE_COLUMNS:
                attributes.set_committed_value(found, name, written[name])

    async def write_step_errors(
        self,
        instance_id: uuid.UUID,
        family: str,
        errors: Mapping[str, BaseException],
        *,
        at: datetime,
        required_state: str | None = None,
    ) -> None:
        """Record failed scope steps in one statement, keeping messages out of storage.

        ``required_state`` lets a caller guard the update without a preceding read. This
        keeps failure recovery to one statement when the failed transaction was rolled back.
        """
        if not errors:
            return
        table = cast(Table, HistoryBackfillScope.__table__)
        statement = (
            update(table)
            .where(
                table.c.instance_id == bindparam("error_instance_id"),
                table.c.family == bindparam("error_family"),
                table.c.scope_key == bindparam("error_scope_key"),
            )
            .values(
                last_error_class=bindparam("error_class"),
                last_error_at=bindparam("error_at"),
            )
        )
        if required_state is not None:
            statement = statement.where(table.c.state == required_state)
        parameters = [
            {
                "error_instance_id": instance_id,
                "error_family": family,
                "error_scope_key": scope_key,
                "error_class": type(exc).__name__[:_MAX_ERROR_CLASS_LENGTH],
                "error_at": at,
            }
            for scope_key, exc in errors.items()
        ]
        await self.session.execute(statement, parameters)
        for values in parameters:
            key = (instance_id, family, values["error_scope_key"])
            found = self.session.identity_map.get(identity_key(HistoryBackfillScope, key))
            if found is not None and required_state is None:
                attributes.set_committed_value(
                    found, "last_error_class", values["error_class"]
                )
                attributes.set_committed_value(found, "last_error_at", at)

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
