"""The QRadar custom-property catalog for the WAF mapping editor.

Guarded at the router by `admin:waf`. Read-only: one cached, slim REST read of
property names per instance; nothing is written and no AQL runs.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.schemas.property_catalog import PropertyCatalogOut
from app.services.property_catalog import stored_catalog

router = APIRouter(prefix="/waf", tags=["waf"])


@router.get("/log-sources/{log_source_id}/property-catalog", response_model=PropertyCatalogOut)
async def get_property_catalog(
    log_source_id: uuid.UUID,
    session: SessionDep,
    refresh: bool = Query(default=False),
    q: str = Query(default="", max_length=512),
    type_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> PropertyCatalogOut:
    log_source = await session.get(LogSource, log_source_id)
    instance = (
        await session.get(QRadarInstance, log_source.instance_id) if log_source else None
    )
    if log_source is None or instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Log source not found")
    return await stored_catalog(
        session,
        instance_id=instance.id,
        source_type_id=type_id if type_id is not None else log_source.type_id,
        q=q, limit=limit,
        refresh=refresh,
    )
