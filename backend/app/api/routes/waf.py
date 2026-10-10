"""WAF collection operations: read-only status for administrators.

Guarded at the router by `admin:waf`. Profile changes and validation are CLI
operations (`python -m app.cli.waf`), audited to the append-only log; nothing
here writes, and nothing here reaches QRadar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.deps import SessionDep, SettingsDep
from app.schemas.waf import WafArielBudgetOut, WafSourceStatusOut
from app.services.waf_status import waf_ariel_budget_status, waf_source_statuses

router = APIRouter(prefix="/waf", tags=["waf"])


@router.get("/sources", response_model=list[WafSourceStatusOut])
async def list_waf_sources(session: SessionDep, settings: SettingsDep) -> list[WafSourceStatusOut]:
    return await waf_source_statuses(session, settings=settings, now=datetime.now(UTC))


@router.get("/ariel-budget", response_model=WafArielBudgetOut)
async def get_waf_ariel_budget(session: SessionDep, settings: SettingsDep) -> WafArielBudgetOut:
    """Global WAF Ariel concurrency: configured limit and permits held now."""
    return await waf_ariel_budget_status(session, settings=settings)


@router.get("/sources/{profile_id}", response_model=WafSourceStatusOut)
async def get_waf_source(
    profile_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> WafSourceStatusOut:
    found = await waf_source_statuses(
        session, settings=settings, now=datetime.now(UTC), profile_id=profile_id
    )
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source profile not found")
    return found[0]
