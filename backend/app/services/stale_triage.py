"""D-55: close NEW analyst triage that nobody took within the configured window.

Only the application's two triage tables are written. Detector-owned anomaly rows and
alerts are read neither for update nor for a lifecycle decision: their ``detected_at``
column is solely the age clock. Every family costs one candidate SELECT, one bulk
UPDATE/INSERT and one bulk activity INSERT, with at most ``STALE_TRIAGE_BATCH_SIZE``
candidates. The next hourly run takes the next batch.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n.messages import MessageRef, render
from app.models.base import utcnow
from app.models.enums import AnomalyFamily, TriageStatus
from app.models.triage import AnomalyTriage
from app.repositories.triage import FAMILY_MODELS

STALE_TRIAGE_BATCH_SIZE = 500
SYSTEM_ACTOR = "system"


@dataclass(frozen=True)
class StaleCandidate:
    anomaly_id: uuid.UUID
    triage_id: uuid.UUID | None
    version: int | None


@dataclass(frozen=True)
class ChangedTriage:
    triage_id: uuid.UUID
    version: int


@dataclass(frozen=True)
class StaleTriageReport:
    status: str
    days: int
    families: dict[str, int]

    @property
    def closed(self) -> int:
        return sum(self.families.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "days": self.days,
            "closed": self.closed,
            "families": dict(self.families),
        }


def stale_close_note(days: int) -> str:
    """The persisted English note, rendered through its stable catalog code."""
    if days < 0:
        raise ValueError("stale close days must not be negative")
    if days == 1:
        return render(MessageRef("triage.stale_auto_close_one"))
    return render(MessageRef("triage.stale_auto_close", {"days": days}))


def stale_eligible(
    *,
    detected_at: datetime,
    triage_status: TriageStatus | None,
    triage_updated_at: datetime | None,
    now: datetime,
    days: int,
) -> bool:
    """The pure age rule; ``triage_status=None`` represents no triage row.

    A row whose state is NEW but whose timestamp is missing is not eligible: an
    unknown clock never becomes permission to close. Equality is old enough.
    """
    if days < 0:
        raise ValueError("stale close days must not be negative")
    if days == 0:
        return False
    cutoff = now - timedelta(days=days)
    if detected_at > cutoff:
        return False
    if triage_status is None:
        return True
    return (
        triage_status is TriageStatus.NEW
        and triage_updated_at is not None
        and triage_updated_at <= cutoff
    )


# The candidate JSON is bounded by STALE_TRIAGE_BATCH_SIZE. The anomaly join repeats
# the age predicate at write time, so an incident deleted between the read and write
# cannot acquire an orphan triage row. The version predicate is the optimistic race
# guard for rows that existed at candidate-read time; ON CONFLICT protects first rows.
_WRITE_SQL = """
WITH candidates AS (
    SELECT *
    FROM jsonb_to_recordset(CAST(:candidates AS jsonb))
        AS c(anomaly_id uuid, triage_id uuid, version integer)
),
updated AS (
    UPDATE anomaly_triage AS t
    SET status = 'CLOSED',
        disposition = 'STALE',
        disposition_note = :note,
        updated_by = 'system',
        updated_at = :now,
        version = t.version + 1
    FROM candidates AS c
    JOIN {table} AS a
      ON a.id = c.anomaly_id AND a.detected_at <= :cutoff
    WHERE t.id = c.triage_id
      AND t.status = 'NEW'
      AND t.version = c.version
      AND t.updated_at <= :cutoff
    RETURNING t.id, t.version
),
inserted AS (
    INSERT INTO anomaly_triage (
        id, family, anomaly_id, owner, status, disposition, disposition_note,
        updated_by, updated_at, version
    )
    SELECT gen_random_uuid(), :family, c.anomaly_id, NULL, 'CLOSED', 'STALE', :note,
           'system', :now, 1
    FROM candidates AS c
    JOIN {table} AS a
      ON a.id = c.anomaly_id AND a.detected_at <= :cutoff
    WHERE c.triage_id IS NULL
    ON CONFLICT (family, anomaly_id) DO NOTHING
    RETURNING id, version
),
changed AS (
    SELECT id, version FROM updated
    UNION ALL
    SELECT id, version FROM inserted
)
SELECT id, version FROM changed
"""

_EVENT_SQL = """
INSERT INTO anomaly_triage_event (
    id, triage_id, kind, actor, created_at, from_value, to_value, comment,
    triage_version
)
SELECT gen_random_uuid(), c.triage_id, 'STATUS', 'system', :now,
       'NEW', 'CLOSED', NULL, c.triage_version
FROM jsonb_to_recordset(CAST(:changed AS jsonb))
    AS c(triage_id uuid, triage_version integer)
"""


class StaleTriageCloser:
    def __init__(
        self,
        session: AsyncSession,
        *,
        days: int,
        batch_size: int = STALE_TRIAGE_BATCH_SIZE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if days < 0:
            raise ValueError("stale close days must not be negative")
        if batch_size < 1:
            raise ValueError("stale triage batch size must be positive")
        self.session = session
        self.days = days
        self.batch_size = batch_size
        self._clock = clock or utcnow

    async def run(self) -> StaleTriageReport:
        if self.days == 0:
            return StaleTriageReport(status="disabled", days=0, families={})
        now = self._clock()
        cutoff = now - timedelta(days=self.days)
        note = stale_close_note(self.days)
        counts: dict[str, int] = {}
        for family in AnomalyFamily:
            candidates = await self._candidates(family, cutoff)
            changed = await self._write_candidates(family, candidates, cutoff, now, note)
            await self._record_events(changed, now)
            counts[family.value] = len(changed)
        await self.session.commit()
        return StaleTriageReport(status="ok", days=self.days, families=counts)

    async def _candidates(
        self, family: AnomalyFamily, cutoff: datetime
    ) -> list[StaleCandidate]:
        model = FAMILY_MODELS[family]
        stmt = (
            select(
                model.id.label("anomaly_id"),
                AnomalyTriage.id.label("triage_id"),
                AnomalyTriage.version.label("version"),
            )
            .select_from(model)
            .outerjoin(
                AnomalyTriage,
                and_(
                    AnomalyTriage.family == family,
                    AnomalyTriage.anomaly_id == model.id,
                ),
            )
            .where(model.detected_at <= cutoff)
            .where(
                or_(
                    AnomalyTriage.id.is_(None),
                    and_(
                        AnomalyTriage.status == TriageStatus.NEW,
                        AnomalyTriage.updated_at <= cutoff,
                    ),
                )
            )
            .order_by(model.detected_at, model.id)
            .limit(self.batch_size)
        )
        rows = (await self.session.execute(stmt)).mappings().all()
        return [
            StaleCandidate(
                anomaly_id=row["anomaly_id"],
                triage_id=row["triage_id"],
                version=row["version"],
            )
            for row in rows
        ]

    async def _write_candidates(
        self,
        family: AnomalyFamily,
        candidates: list[StaleCandidate],
        cutoff: datetime,
        now: datetime,
        note: str,
    ) -> list[ChangedTriage]:
        payload = json.dumps(
            [
                {
                    "anomaly_id": str(candidate.anomaly_id),
                    "triage_id": (
                        str(candidate.triage_id) if candidate.triage_id is not None else None
                    ),
                    "version": candidate.version,
                }
                for candidate in candidates
            ]
        )
        table = FAMILY_MODELS[family].__tablename__
        rows = (
            (
                await self.session.execute(
                    text(_WRITE_SQL.format(table=table)),
                    {
                        "candidates": payload,
                        "family": family.value,
                        "cutoff": cutoff,
                        "now": now,
                        "note": note,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            ChangedTriage(triage_id=row["id"], version=row["version"]) for row in rows
        ]

    async def _record_events(self, changed: list[ChangedTriage], now: datetime) -> None:
        payload = json.dumps(
            [
                {"triage_id": str(row.triage_id), "triage_version": row.version}
                for row in changed
            ]
        )
        await self.session.execute(text(_EVENT_SQL), {"changed": payload, "now": now})
