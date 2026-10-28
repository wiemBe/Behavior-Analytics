"""Phase C.2b catch-all senders API: the review queue and the per-bucket history.

Reads need `read:fleet` (router-level). Review and dismiss are fleet-review acts
and need `log_source:write`, as `POST /log-sources/{id}/review` does. Both are
audited with the principal as actor and are idempotent: a sender already in the
target state is returned unchanged, with no second audit row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import catchall_sender_fingerprint
from app.alerts.routing import default_policy
from app.alerts.service import AlertResolution, AlertService
from app.api.deps import SessionDep, SettingsDep
from app.history.origin import origin_of
from app.history.state import HistoryFamily
from app.i18n.refusal import permission_refusal
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.enums import CatchallReviewStatus
from app.models.log_source import LogSource
from app.repositories.audit import record_audit
from app.schemas.catchall import (
    CatchallBucketList,
    CatchallBucketOut,
    CatchallSenderOut,
    CatchallSenderPage,
    CatchallSourceList,
    CatchallSourceOut,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_LOG_SOURCE_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)

router = APIRouter(prefix="/fleet/catchall", tags=["fleet"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]

#: The longest span `/buckets` answers: a week of 5-minute buckets is 2,016 rows.
MAX_BUCKET_RANGE = timedelta(days=7)


_AUDIT_ACTIONS = {
    CatchallReviewStatus.REVIEWED: "catchall_sender.review",
    CatchallReviewStatus.DISMISSED: "catchall_sender.dismiss",
}


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


def _escape_like(value: str) -> str:
    """Make `%`, `_` and the escape character match themselves."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sender_out(sender: CatchallSender, source_name: str) -> CatchallSenderOut:
    return CatchallSenderOut(
        id=sender.id,
        catchall_log_source_id=sender.catchall_log_source_id,
        catchall_log_source_name=source_name,
        identifier=sender.identifier,
        first_seen_at=sender.first_seen_at,
        last_seen_at=sender.last_seen_at,
        events_24h=sender.events_24h,
        active=sender.active,
        review_status=CatchallReviewStatus(sender.review_status),
        reviewed_by=sender.reviewed_by,
        reviewed_at=sender.reviewed_at,
    )


def _bucket_out(row: CatchallBucket) -> CatchallBucketOut:
    """The stored bucket as the API reports it, labelled with where it came from.

    The marker is read with the shared helper, so the two spellings of "this row
    came from QRadar's past" stay in one place (HISTORY-BACKFILL §B.4.4).
    """
    out = CatchallBucketOut.model_validate(row)
    out.origin = origin_of(HistoryFamily.CATCHALL, row.origin)
    return out


@router.get("/sources", response_model=CatchallSourceList)
async def list_sources(session: SessionDep, settings: SettingsDep) -> CatchallSourceList:
    """The catch-all sources: each log source of a catch-all type, in one statement.

    `latest_bucket_start` is null when no bucket was ever measured for the source.
    """
    type_ids = sorted(set(settings.catchall_type_ids))
    latest = (
        select(func.max(CatchallBucket.bucket_start))
        .where(CatchallBucket.catchall_log_source_id == LogSource.id)
        .scalar_subquery()
    )
    new_senders = (
        select(func.count())
        .select_from(CatchallSender)
        .where(
            CatchallSender.catchall_log_source_id == LogSource.id,
            CatchallSender.review_status == CatchallReviewStatus.NEW.value,
        )
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(LogSource.id, LogSource.name, LogSource.type_name, latest, new_senders)
            .where(LogSource.type_id.in_(type_ids))
            .order_by(LogSource.name, LogSource.id)
        )
    ).all()
    return CatchallSourceList(
        catchall_type_ids=type_ids,
        items=[
            CatchallSourceOut(
                id=row[0],
                name=row[1],
                type_name=row[2],
                latest_bucket_start=row[3],
                new_sender_count=row[4] or 0,
            )
            for row in rows
        ],
    )


@router.get("/senders", response_model=CatchallSenderPage)
async def list_senders(
    session: SessionDep,
    settings: SettingsDep,
    review_status: CatchallReviewStatus | None = Query(default=None),
    catchall_log_source_id: uuid.UUID | None = Query(default=None),
    q: str | None = Query(default=None, min_length=1, max_length=255),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> CatchallSenderPage:
    """The senders queue: NEW first, then the busiest by 24 h events.

    `q` is a literal substring of the identifier (`%` and `_` match themselves).
    A sender whose 24 h events are unknown (null) sorts after every known one.
    """
    bounded = max(1, min(limit, settings.api_max_page_size))
    conditions = []
    if review_status is not None:
        conditions.append(CatchallSender.review_status == review_status.value)
    if catchall_log_source_id is not None:
        conditions.append(CatchallSender.catchall_log_source_id == catchall_log_source_id)
    if q is not None:
        conditions.append(CatchallSender.identifier.ilike(f"%{_escape_like(q)}%", escape="\\"))

    total = await session.scalar(
        select(func.count()).select_from(CatchallSender).where(*conditions)
    )
    new_first = case((CatchallSender.review_status == CatchallReviewStatus.NEW.value, 0), else_=1)
    rows = (
        await session.execute(
            select(CatchallSender, LogSource.name)
            .join(LogSource, LogSource.id == CatchallSender.catchall_log_source_id)
            .where(*conditions)
            .order_by(
                new_first,
                CatchallSender.events_24h.desc().nulls_last(),
                CatchallSender.identifier,
                CatchallSender.id,
            )
            .limit(bounded)
            .offset(offset)
        )
    ).all()
    return CatchallSenderPage(
        items=[_sender_out(sender, name) for sender, name in rows],
        total=total or 0,
        limit=bounded,
        offset=offset,
    )


@router.get("/buckets", response_model=CatchallBucketList)
async def list_buckets(
    session: SessionDep,
    catchall_log_source_id: uuid.UUID,
    since: datetime,
    until: datetime,
) -> CatchallBucketList:
    """Measured buckets of one catch-all source in `[since, until)`, oldest first.

    A bucket that was not measured has no row: a gap is unknown, not zero.
    """
    if since.tzinfo is None or until.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "since and until need a timezone"
        )
    if until <= since:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "until must be after since")
    if until - since > MAX_BUCKET_RANGE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"the range is at most {MAX_BUCKET_RANGE.days} days",
        )
    if await session.get(LogSource, catchall_log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    rows = (
        await session.scalars(
            select(CatchallBucket)
            .where(
                CatchallBucket.catchall_log_source_id == catchall_log_source_id,
                CatchallBucket.bucket_start >= since,
                CatchallBucket.bucket_start < until,
            )
            .order_by(CatchallBucket.bucket_start)
        )
    ).all()
    return CatchallBucketList(
        catchall_log_source_id=catchall_log_source_id,
        since=since,
        until=until,
        items=[_bucket_out(row) for row in rows],
    )


async def _transition(
    sender_id: uuid.UUID,
    target: CatchallReviewStatus,
    session: SessionDep,
    principal: Principal,
) -> CatchallSenderOut:
    _guard(principal, PERM_LOG_SOURCE_WRITE)
    sender = await session.get(CatchallSender, sender_id)
    if sender is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sender not found")
    source = await session.get(LogSource, sender.catchall_log_source_id)
    assert source is not None  # the foreign key guarantees it
    if sender.review_status != target.value:
        now = datetime.now(UTC)
        previous = sender.review_status
        sender.review_status = target.value  # type: ignore[assignment]
        sender.reviewed_by = principal.subject
        sender.reviewed_at = now
        await session.flush()
        _, resolved = await AlertService(session, clock=lambda: now).apply_bulk(
            [],
            [
                AlertResolution(
                    fingerprint=catchall_sender_fingerprint(
                        sender.instance_id, sender.catchall_log_source_id, sender.identifier
                    ),
                    actor=principal.subject,
                    reason=f"catch-all sender {target.value.lower()}",
                )
            ],
        )
        result = resolved[0]
        if result is not None and result.transition is not None:
            # Enqueue only: delivery belongs to the dispatch worker.
            await NotificationDispatcher(
                session, notifiers={}, policy=default_policy()
            ).enqueue(result.alert, result.transition)
        await record_audit(
            session,
            action=_AUDIT_ACTIONS[target],
            actor=principal.subject,
            object_type="catchall_sender",
            object_id=str(sender.id),
            detail={
                "from": previous,
                "to": target.value,
                "catchall_log_source": source.name,
                "alert_resolved": result is not None,
            },
        )
    return _sender_out(sender, source.name)


@router.post("/senders/{sender_id}/review", response_model=CatchallSenderOut)
async def review_sender(
    sender_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> CatchallSenderOut:
    """Mark a sender reviewed and resolve its new-sender alert, if one was raised."""
    return await _transition(sender_id, CatchallReviewStatus.REVIEWED, session, principal)


@router.post("/senders/{sender_id}/dismiss", response_model=CatchallSenderOut)
async def dismiss_sender(
    sender_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> CatchallSenderOut:
    """Dismiss a sender: it stays tracked, and leaves the NEW queue."""
    return await _transition(sender_id, CatchallReviewStatus.DISMISSED, session, principal)
