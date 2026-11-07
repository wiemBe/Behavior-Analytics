"""The triage queue (SOC-4b): what is waiting on an analyst, from five sources.

`GET /queue` needs an authenticated principal and nothing more. It is deliberately not behind
one router-level permission: that would make the page all or nothing. Each source checks its
own read permission and reports `FORBIDDEN`, naming it, when the caller lacks it.

The comparison instant for "new" is the `since` input (an ISO UTC timestamp). Nothing is
stored or advanced here: a GET with a side effect is forbidden, so moving the marker is a
separate write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.api.routes.anomalies import PrincipalDep
from app.models.enums import AnomalyFamily, Severity
from app.schemas.queue import QueueKind, QueueOut, QueueScope
from app.services.queue import MAX_DEPTH, build_queue

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
