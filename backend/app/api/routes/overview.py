"""SOC overview API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import EffectiveSettingsDep, SessionDep
from app.schemas.log_source import SocOverview
from app.services.overview import OverviewService

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("", response_model=SocOverview)
async def soc_overview(session: SessionDep, settings: EffectiveSettingsDep) -> SocOverview:
    return await OverviewService(session, settings).build()
