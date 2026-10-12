"""The QRadar custom-property catalog for the WAF mapping editor.

Guarded at the router by `admin:waf`. Read-only: one cached, slim REST read of
property names per instance; nothing is written and no AQL runs.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.factory import build_provider_for_instance
from app.schemas.property_catalog import PropertyCatalogOut
from app.services.property_catalog import PropertyCatalogService, get_property_catalog_service

router = APIRouter(prefix="/waf", tags=["waf"])


@router.get("/log-sources/{log_source_id}/property-catalog", response_model=PropertyCatalogOut)
async def get_property_catalog(
    log_source_id: uuid.UUID,
    session: SessionDep,
    service: Annotated[PropertyCatalogService, Depends(get_property_catalog_service)],
    refresh: bool = Query(default=False),
) -> PropertyCatalogOut:
    log_source = await session.get(LogSource, log_source_id)
    instance = (
        await session.get(QRadarInstance, log_source.instance_id) if log_source else None
    )
    if log_source is None or instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Log source not found")
    return await service.catalog(
        instance_id=instance.id,
        provider_factory=lambda: build_provider_for_instance(instance),
        source_type_id=log_source.type_id,
        refresh=refresh,
    )
