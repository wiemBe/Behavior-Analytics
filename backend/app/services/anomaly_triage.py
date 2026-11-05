"""Analyst triage rules and their persistence (SOC-1a).

The transition rules are a pure function (`plan_patch`) so every guard can be pinned
without a database. The service around it owns the transaction shape: the anomaly must
exist, the triage row is locked while its version is checked, and one state-changing
PATCH increments the version exactly once.

The decision this module records, because the task asks for one: **a disposition may be
set while the item is not being closed.** An analyst often knows a source is noisy
before they write the closing note; forbidding that would force busywork. It stays
optional until CLOSED, where it becomes required. Changing the disposition without
sending a note clears the old note: the note explains one disposition, and carrying it
over to another would misattribute it. The cleared value remains in the activity log.

Reopening is explicit: a CLOSED item can only be changed by a PATCH that names
`status=IN_PROGRESS`. The disposition, its note and the whole event history are kept.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.enums import (
    AnomalyFamily,
    TriageDisposition,
    TriageEventKind,
    TriageStatus,
)
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.repositories.triage import TriageRepository
from app.schemas.triage import (
    PagedTriageEvents,
    TriageEventOut,
    TriageOut,
    TriagePatchIn,
)


class TriageError(Exception):
    """Base class for a triage request the route must refuse."""


class TriageNotFound(TriageError):
    """The family is known but no anomaly with that id exists."""


class TriageConflict(TriageError):
    """The expected version is not the stored one, or a concurrent insert won."""


class TriageInvalid(TriageError):
    """The requested state change is not allowed."""


@dataclass(frozen=True)
class PlannedEvent:
    kind: TriageEventKind
    from_value: str | None = None
    to_value: str | None = None
    comment: str | None = None


@dataclass(frozen=True)
class TriagePlan:
    owner: str | None
    status: TriageStatus
    disposition: TriageDisposition | None
    disposition_note: str | None
    events: list[PlannedEvent] = field(default_factory=list)


def _text(value: TriageStatus | TriageDisposition | None) -> str | None:
    return value.value if value is not None else None


def plan_patch(row: AnomalyTriage | None, payload: TriagePatchIn) -> TriagePlan:
    """Validate one PATCH and list the events it must append.

    Raises `TriageInvalid` for a forbidden transition. An empty event list means the
    request asked for no actual change; the caller returns the current state without
    touching the version.
    """
    provided = payload.model_fields_set
    owner_given = "owner" in provided
    status_given = "status" in provided
    disposition_given = "disposition" in provided
    note_given = "disposition_note" in provided
    if not any((owner_given, status_given, disposition_given, note_given)):
        raise TriageInvalid("no triage field to update")

    current_owner = row.owner if row is not None else None
    current_status = TriageStatus(row.status) if row is not None else TriageStatus.NEW
    current_disposition = (
        TriageDisposition(row.disposition)
        if row is not None and row.disposition is not None
        else None
    )
    current_note = row.disposition_note if row is not None else None

    if status_given:
        if payload.status is None:
            raise TriageInvalid("status cannot be cleared")
        result_status = payload.status
        if result_status is TriageStatus.NEW and current_status is not TriageStatus.NEW:
            raise TriageInvalid(
                "status NEW is the state of an anomaly with no triage; use IN_PROGRESS"
            )
    else:
        result_status = current_status
    if current_status is TriageStatus.CLOSED and not status_given:
        raise TriageInvalid(
            "a closed anomaly can only be changed by reopening it with an explicit "
            "status change"
        )

    # A new disposition without a new note clears the old note: it described the old
    # disposition.
    if disposition_given and payload.disposition != current_disposition and not note_given:
        result_note = None
    else:
        result_note = payload.disposition_note if note_given else current_note
    result_disposition = (
        payload.disposition if disposition_given else current_disposition
    )
    if result_status is TriageStatus.CLOSED and result_disposition is None:
        raise TriageInvalid("closing an anomaly requires a disposition")

    events: list[PlannedEvent] = []
    if owner_given and payload.owner != current_owner:
        events.append(
            PlannedEvent(
                TriageEventKind.OWNER,
                from_value=current_owner,
                to_value=payload.owner,
            )
        )
    if status_given and result_status is not current_status:
        events.append(
            PlannedEvent(
                TriageEventKind.STATUS,
                from_value=_text(current_status),
                to_value=_text(result_status),
            )
        )
    disposition_changed = disposition_given and payload.disposition != current_disposition
    if disposition_changed:
        events.append(
            PlannedEvent(
                TriageEventKind.DISPOSITION,
                from_value=_text(current_disposition),
                to_value=_text(payload.disposition),
                comment=result_note,
            )
        )
    if note_given and not disposition_changed and payload.disposition_note != current_note:
        events.append(
            PlannedEvent(TriageEventKind.NOTE, comment=payload.disposition_note)
        )

    return TriagePlan(
        owner=payload.owner if owner_given else current_owner,
        status=result_status,
        disposition=result_disposition,
        disposition_note=result_note,
        events=events,
    )


class AnomalyTriageService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.repo = TriageRepository(session)
        self._clock = clock or utcnow

    async def get(
        self,
        family: AnomalyFamily,
        anomaly_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> TriageOut:
        if not await self.repo.anomaly_exists(family, anomaly_id):
            raise TriageNotFound("anomaly not found")
        row = await self.repo.row(family, anomaly_id)
        return await self._out(family, anomaly_id, row, limit=limit, offset=offset)

    async def update(
        self,
        family: AnomalyFamily,
        anomaly_id: uuid.UUID,
        payload: TriagePatchIn,
        *,
        actor: str,
    ) -> tuple[TriageOut, list[PlannedEvent]]:
        """Apply one PATCH and return the new state plus the events it appended.

        The event list is what the route audits: field names and from-to values only,
        never a note's text.
        """
        if not await self.repo.anomaly_exists(family, anomaly_id):
            raise TriageNotFound("anomaly not found")
        row = await self.repo.row(family, anomaly_id, for_update=True)
        current_version = row.version if row is not None else 0
        if payload.expected_version != current_version:
            raise TriageConflict(
                "the triage changed since it was read; reload it and retry"
            )
        plan = plan_patch(row, payload)
        if not plan.events:
            return await self._out(
                family, anomaly_id, row, limit=50, offset=0
            ), []

        now = self._clock()
        if row is None:
            row = AnomalyTriage(
                family=family,
                anomaly_id=anomaly_id,
                owner=plan.owner,
                status=plan.status,
                disposition=plan.disposition,
                disposition_note=plan.disposition_note,
                updated_by=actor,
                updated_at=now,
                version=1,
            )
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                # Two first PATCHes raced. The loser reports a stale version, the same
                # answer it would have received one request later.
                await self.session.rollback()
                raise TriageConflict(
                    "the triage was created concurrently; reload it and retry"
                ) from exc
        else:
            row.owner = plan.owner
            row.status = plan.status
            row.disposition = plan.disposition
            row.disposition_note = plan.disposition_note
            row.updated_by = actor
            row.updated_at = now
            row.version += 1

        for event in plan.events:
            self.session.add(
                AnomalyTriageEvent(
                    triage_id=row.id,
                    kind=event.kind,
                    actor=actor,
                    created_at=now,
                    from_value=event.from_value,
                    to_value=event.to_value,
                    comment=event.comment,
                    triage_version=row.version,
                )
            )
        await self.session.flush()
        return await self._out(family, anomaly_id, row, limit=50, offset=0), plan.events

    async def comment(
        self,
        family: AnomalyFamily,
        anomaly_id: uuid.UUID,
        text: str,
        *,
        actor: str,
    ) -> TriageEventOut:
        if not await self.repo.anomaly_exists(family, anomaly_id):
            raise TriageNotFound("anomaly not found")
        row = await self.repo.row(family, anomaly_id, for_update=True)
        now = self._clock()
        if row is None:
            row = AnomalyTriage(
                family=family,
                anomaly_id=anomaly_id,
                status=TriageStatus.NEW,
                updated_by=actor,
                updated_at=now,
                version=1,
            )
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                await self.session.rollback()
                row = await self.repo.row(family, anomaly_id, for_update=True)
                if row is None:  # pragma: no cover - the winner is visible after rollback
                    raise TriageConflict(
                        "the triage changed concurrently; reload it and retry"
                    ) from exc
        event = AnomalyTriageEvent(
            triage_id=row.id,
            kind=TriageEventKind.COMMENT,
            actor=actor,
            created_at=now,
            comment=text,
            triage_version=row.version,
        )
        self.session.add(event)
        await self.session.flush()
        return TriageEventOut.model_validate(event)

    async def _out(
        self,
        family: AnomalyFamily,
        anomaly_id: uuid.UUID,
        row: AnomalyTriage | None,
        *,
        limit: int,
        offset: int,
    ) -> TriageOut:
        if row is None:
            events: list[AnomalyTriageEvent] = []
            total = 0
        else:
            events, total = await self.repo.events_page(
                row.id, limit=limit, offset=offset
            )
        return TriageOut(
            family=family,
            anomaly_id=anomaly_id,
            status=row.status if row is not None else TriageStatus.NEW,
            owner=row.owner if row is not None else None,
            disposition=row.disposition if row is not None else None,
            disposition_note=row.disposition_note if row is not None else None,
            updated_by=row.updated_by if row is not None else None,
            updated_at=row.updated_at if row is not None else None,
            version=row.version if row is not None else 0,
            events=PagedTriageEvents(
                items=[TriageEventOut.model_validate(event) for event in events],
                total=total,
                limit=limit,
                offset=offset,
            ),
        )
