"""The triage queue (SOC-4b): what is waiting on an analyst, from five sources.

`GET /queue` needs an authenticated principal and nothing more. It is deliberately not behind
one router-level permission: that would make the page all or nothing. Each source checks its
own read permission and reports `FORBIDDEN`, naming it, when the caller lacks it.

The comparison instant for "new" is the `since` input (an ISO UTC timestamp). Nothing is
stored or advanced by the GET: a GET with a side effect is forbidden, so moving the caller's
own marker is the separate `POST /queue/seen`, which needs no permission beyond a principal
(it touches only the caller's row, like `PUT /me/preferences`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.api.routes.anomalies import PrincipalDep
from app.models.enums import AnomalyFamily, Severity
from app.schemas.queue import MarkerOut, MarkerSeenIn, QueueKind, QueueOut, QueueScope
from app.services.queue import MAX_DEPTH, build_queue
from app.services.queue_marker import advance

router = APIRouter(prefix="/queue", tags=["queue"])


@router.get("", response_model=QueueOut)
async def get_queue(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    scope: QueueScope = Query(default=QueueScope.ALL),
    kind: Annotated[list[QueueKind] | None, Query()] = None,
    family: Annotated[list[AnomalyFamily] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    new_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> QueueOut:
    """One ordered page of waiting work, one status per source.

    Ordered by severity descending, then the longest wait first. A `family` filter without a
    `kind` filter narrows the queue to anomalies, the only kind that has a family.
    """
    effective_limit = min(limit, settings.api_max_page_size)
    if offset + effective_limit > MAX_DEPTH:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"offset + limit must not exceed {MAX_DEPTH}: narrow the queue with a filter",
        )
    now = datetime.now(UTC)
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if since > now:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "since must not be in the future"
            )
        if (now - since).days > settings.api_max_range_days:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"since is older than the maximum of {settings.api_max_range_days} days",
            )
    kinds = list(kind or [])
    if family and not kinds:
        kinds = [QueueKind.ANOMALY]
    return await build_queue(
        session,
        settings,
        principal,
        scope=scope,
        kinds=kinds,
        families=family or [],
        severities=severity or [],
        new_only=new_only,
        since=since,
        limit=effective_limit,
        offset=offset,
        now=now,
    )


@router.post("/seen", response_model=MarkerOut)
async def post_seen(body: MarkerSeenIn, session: SessionDep, principal: PrincipalDep) -> MarkerOut:
    """Move the caller's marker to the `as_of` of the queue response they just loaded.

    Compare-and-set on `expected`; a mismatch or a failed source is a 409 that leaves the
    marker where it was. An `as_of` older than the stored marker is a no-op 200.
    """
    result = await advance(
        session,
        principal.subject,
        as_of=body.as_of,
        expected=body.expected,
        all_sources_ok=body.all_sources_ok,
    )
    return MarkerOut(
        marked_at=result.state.marked_at,
        previous_marked_at=result.state.previous_marked_at,
        advanced=result.advanced,
    )
