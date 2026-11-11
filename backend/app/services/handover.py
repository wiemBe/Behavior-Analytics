"""Build and store immutable handovers without refreshing their frozen queue data."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.alert import Alert
from app.models.handover import ShiftHandover
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.repositories.audit import record_audit
from app.schemas.handover import (
    ActedOnItem,
    ActedOnSource,
    HandoverBody,
    HandoverItem,
    HandoverListOut,
    HandoverOut,
    HandoverSummary,
    HandoverWatchIn,
)
from app.schemas.queue import QueueKind, QueueRow, QueueScope, SourceState
from app.security.rbac import PERM_ALERT_READ, PERM_ANOMALY_READ, Principal
from app.services.queue import build_queue

logger = logging.getLogger(__name__)

MAX_ITEMS = 50
MAX_ACTIONS = 50
MAX_BODY_BYTES = 256 * 1024
MAX_TITLE_LENGTH = 200


def _out(row: ShiftHandover) -> HandoverOut:
    return HandoverOut(
        id=row.id,
        author=row.author_subject,
        written_at=row.written_at,
        window_started_at=row.window_started_at,
        previous_id=row.previous_id,
        note=row.note,
        body=HandoverBody.model_validate(row.body),
    )


async def latest(session: AsyncSession) -> ShiftHandover | None:
    return await session.scalar(
        select(ShiftHandover)
        .order_by(ShiftHandover.written_at.desc(), ShiftHandover.id.desc())
        .limit(1)
    )


async def get(session: AsyncSession, handover_id: uuid.UUID) -> HandoverOut | None:
    row = await session.get(ShiftHandover, handover_id)
    return _out(row) if row is not None else None


async def list_handovers(
    session: AsyncSession, *, limit: int, offset: int
) -> HandoverListOut:
    total = int(await session.scalar(select(func.count()).select_from(ShiftHandover)) or 0)
    rows = (
        await session.execute(
            select(
                ShiftHandover.id,
                ShiftHandover.author_subject,
                ShiftHandover.written_at,
                ShiftHandover.window_started_at,
                ShiftHandover.previous_id,
                ShiftHandover.note,
            )
            .order_by(ShiftHandover.written_at.desc(), ShiftHandover.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return HandoverListOut(
        items=[
            HandoverSummary(
                id=row.id,
                author=row.author_subject,
                written_at=row.written_at,
                window_started_at=row.window_started_at,
                previous_id=row.previous_id,
                note_length=len(row.note),
                note_excerpt=row.note[:240],
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _guarded_actions(
    session: AsyncSession,
    kind: str,
    permission: str,
    principal: Principal,
    work: Callable[[], Awaitable[list[ActedOnItem]]],
) -> tuple[ActedOnSource, list[ActedOnItem]]:
    if not principal.has(permission):
        return (
            ActedOnSource(
                kind=kind,
                state=SourceState.FORBIDDEN,
                count=None,
                permission=permission,
                reason_code=None,
            ),
            [],
        )
    try:
        async with session.begin_nested():
            items = await work()
        return (
            ActedOnSource(
                kind=kind,
                state=SourceState.OK,
                count=len(items),
                permission=None,
                reason_code=None,
            ),
            items,
        )
    except Exception:
        logger.exception("handover acted-on source %s failed to load", kind)
        return (
            ActedOnSource(
                kind=kind,
                state=SourceState.FAILED,
                count=None,
                permission=None,
                reason_code="LOAD_FAILED",
            ),
            [],
        )


async def _triage_actions(
    session: AsyncSession, subject: str, start: datetime, end: datetime
) -> list[ActedOnItem]:
    rows = (
        await session.execute(
            select(
                AnomalyTriageEvent.id,
                AnomalyTriage.anomaly_id,
                AnomalyTriageEvent.kind,
                AnomalyTriageEvent.created_at,
                AnomalyTriageEvent.from_value,
                AnomalyTriageEvent.to_value,
            )
            .join(AnomalyTriage, AnomalyTriage.id == AnomalyTriageEvent.triage_id)
            .where(
                AnomalyTriageEvent.actor == subject,
                AnomalyTriageEvent.created_at >= start,
                AnomalyTriageEvent.created_at <= end,
            )
            .order_by(AnomalyTriageEvent.created_at.desc(), AnomalyTriageEvent.id.desc())
            .limit(MAX_ACTIONS)
        )
    ).all()
    return [
        ActedOnItem(
            kind="triage",
            id=str(row.id),
            related_id=str(row.anomaly_id),
            link=f"/anomalies/{row.anomaly_id}",
            event=str(row.kind),
            at=row.created_at,
            from_value=row.from_value,
            to_value=row.to_value,
        )
        for row in rows
    ]


async def _alert_actions(
    session: AsyncSession, subject: str, start: datetime, end: datetime
) -> list[ActedOnItem]:
    acknowledged = select(
        Alert.id.label("id"),
        literal("ACKNOWLEDGED").label("event"),
        Alert.acknowledged_at.label("at"),
        literal("OPEN").label("from_value"),
        literal("ACKNOWLEDGED").label("to_value"),
    ).where(
        Alert.acknowledged_by == subject,
        Alert.acknowledged_at >= start,
        Alert.acknowledged_at <= end,
    )
    resolved = select(
        Alert.id.label("id"),
        literal("RESOLVED").label("event"),
        Alert.resolved_at.label("at"),
        case(
            (Alert.acknowledged_at.is_not(None), literal("ACKNOWLEDGED")),
            else_=literal("OPEN"),
        ).label("from_value"),
        literal("RESOLVED").label("to_value"),
    ).where(
        Alert.resolved_by == subject,
        Alert.resolved_at >= start,
        Alert.resolved_at <= end,
    )
    actions = union_all(acknowledged, resolved).subquery()
    rows = (
        await session.execute(
            select(actions)
            .order_by(actions.c.at.desc(), actions.c.id.desc())
            .limit(MAX_ACTIONS)
        )
    ).all()
    return [
        ActedOnItem(
            kind="alert",
            id=f"{row.id}:{row.event}",
            related_id=str(row.id),
            link=f"/alerts/{row.id}",
            event=row.event,
            at=row.at,
            from_value=row.from_value,
            to_value=row.to_value,
        )
        for row in rows
    ]


def _snapshot_items(
    queue_items: Sequence[QueueRow], settings: Settings, watch: set[tuple[str, str]]
) -> list[HandoverItem]:
    items: list[HandoverItem] = []
    for row in queue_items:
        key = (row.kind.value, row.id)
        is_offense = row.kind is QueueKind.OFFENSE
        items.append(
            HandoverItem(
                kind=row.kind,
                family=row.family,
                id=row.id,
                link=row.link,
                state=row.state,
                owner=row.owner,
                owner_kind=row.owner_kind,
                severity=row.severity,
                waiting_since=row.waiting_since,
                age_seconds=row.age_seconds,
                change=row.change,
                watch=key in watch,
                over_sla=(
                    row.age_seconds >= settings.offense_sla_hours * 3600 if is_offense else None
                ),
                title=row.title[:MAX_TITLE_LENGTH],
                title_code=row.title_code,
                title_params=row.title_params,
            )
        )
    return items


async def build_body(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    *,
    watch: Sequence[HandoverWatchIn] = (),
    now: datetime | None = None,
) -> tuple[HandoverBody, ShiftHandover | None]:
    as_of = now or datetime.now(UTC)
    previous = await latest(session)
    window_started_at = previous.written_at if previous else as_of - timedelta(hours=24)
    queue = await build_queue(
        session,
        settings,
        principal,
        scope=QueueScope.ALL,
        kinds=[],
        families=[],
        severities=[],
        new_only=False,
        since=window_started_at,
        limit=MAX_ITEMS,
        offset=0,
        now=as_of,
        read_marker=False,
    )
    wanted = {(item.kind.value, item.id) for item in watch}
    present = {(item.kind.value, item.id) for item in queue.items}
    dropped = [item for item in watch if (item.kind.value, item.id) not in present]
    items = _snapshot_items(queue.items, settings, wanted & present)

    triage_source, triage_items = await _guarded_actions(
        session,
        "triage",
        PERM_ANOMALY_READ,
        principal,
        lambda: _triage_actions(session, principal.subject, window_started_at, as_of),
    )
    alert_source, alert_items = await _guarded_actions(
        session,
        "alert",
        PERM_ALERT_READ,
        principal,
        lambda: _alert_actions(session, principal.subject, window_started_at, as_of),
    )
    body = HandoverBody(
        as_of=queue.as_of,
        window_started_at=window_started_at,
        sources=queue.sources,
        total=queue.total,
        total_exact=queue.total_exact,
        truncated=queue.total > MAX_ITEMS or not queue.total_exact,
        items=items,
        acted_on=sorted(
            [*triage_items, *alert_items], key=lambda item: item.at, reverse=True
        ),
        acted_on_sources=[triage_source, alert_source],
        watch_dropped=dropped,
    )
    encoded = json.dumps(body.model_dump(mode="json"), separators=(",", ":")).encode()
    if len(encoded) > MAX_BODY_BYTES:
        raise ValueError("handover body exceeds 256 KiB")
    return body, previous


async def create(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    *,
    note: str,
    watch: Sequence[HandoverWatchIn],
) -> HandoverOut:
    body, previous = await build_body(session, settings, principal, watch=watch)
    row = ShiftHandover(
        author_subject=principal.subject,
        written_at=body.as_of,
        window_started_at=body.window_started_at,
        previous_id=previous.id if previous else None,
        note=note,
        body=body.model_dump(mode="json"),
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        action="handover.create",
        actor=principal.subject,
        object_type="shift_handover",
        object_id=str(row.id),
        detail={
            "note_length": len(note),
            "item_count": len(body.items),
            "watch_count": sum(item.watch for item in body.items),
            "acted_count": len(body.acted_on),
            "truncated": body.truncated,
            "previous_id": str(row.previous_id) if row.previous_id else None,
            "sources": {source.kind.value: source.state.value for source in body.sources},
        },
    )
    return _out(row)
