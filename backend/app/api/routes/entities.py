"""Stored entity timeline API.

The route resolves one console, validates the bounded read and delegates to a
database-only service. It never receives AQL and never constructs a provider.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.instance import QRadarInstance
from app.schemas.entity_timeline import EntityTimelineOut
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.entity_timeline import (
    DEFAULT_LIMIT,
    EntityTimelineInputError,
    EntityTimelineService,
    resolve_entity,
    validate_limit,
    window_bounds,
)

router = APIRouter(prefix="/entities", tags=["entities"])
PrincipalDep = Annotated[Principal, Depends(get_principal)]


@router.get("/timeline", response_model=EntityTimelineOut)
async def get_entity_timeline(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    value: str = Query(min_length=1, max_length=255),
    kind: str | None = Query(default=None, max_length=8),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT),
    cursor: str | None = Query(default=None, max_length=2048),
    instance_id: uuid.UUID | None = Query(default=None),
) -> EntityTimelineOut:
    """What this console has already stored about one typed entity.

    ``instance_id`` follows the neighboring offense, rule and WAF reads: when
    omitted, the first configured console is used. Supplying it avoids that one
    resolution statement and keeps the service's fixed ``3 + H`` cost visible.
    """
    try:
        resolved_kind, cleaned, value_norm = resolve_entity(kind, value)
        resolved_start, resolved_end = window_bounds(start, end)
        bounded_limit = validate_limit(limit)
        resolved_instance = instance_id or await session.scalar(
            select(QRadarInstance.id).limit(1)
        )
        if resolved_instance is None:
            raise EntityTimelineInputError(
                MessageRef("entity_timeline.input.instance_missing")
            )
        return await EntityTimelineService(session, settings, principal).read(
            instance_id=resolved_instance,
            kind=resolved_kind,
            value=cleaned,
            value_norm=value_norm,
            start=resolved_start,
            end=resolved_end,
            limit=bounded_limit,
            cursor=cursor,
        )
    except EntityTimelineInputError as exc:
        raise CodedHTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.ref) from exc
