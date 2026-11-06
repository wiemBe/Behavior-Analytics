"""Noisy-sources list API (SOC-17a, design §2.2).

Two read-only routes, mounted with the other behaviour reads under
``read:anomalies``. The list answers "which sources keep firing, and how much of
it is noise" in three statements; the detail route answers the same for one
source and adds its per-detector breakdown and current settings.

Nothing here writes, and neither route accepts a source filter, an AQL string or
a detector name from the client: the only inputs are a window, an order and a
page, so no caller can turn this read into an ad-hoc query surface.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.noise.backtest import NoiseBacktest
from app.noise.read_model import WINDOW_DAYS, NoiseOrder, NoiseReadModel
from app.schemas.noise import (
    NoiseListOut,
    NoisePreviewIn,
    NoisePreviewOut,
    NoiseSourceDetailOut,
    detail_out,
    list_out,
    preview_out,
)

router = APIRouter(prefix="/noise", tags=["noise"])


@router.get("/sources", response_model=NoiseListOut)
async def list_noise_sources(
    session: SessionDep,
    settings: SettingsDep,
    window: Annotated[int, Query()] = 28,
    order: Annotated[NoiseOrder, Query()] = NoiseOrder.INCIDENTS,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NoiseListOut:
    """The ranked noisy-sources list, with the quiet count beside it.

    Three statements whatever the fleet size. The page is cut here, after the
    fold, because the order is by a computed share as well as by real counts;
    the grouped reads themselves never run per row.
    """
    if window not in WINDOW_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "window must be 7 or 28 days",
        )
    listing = await NoiseReadModel(session).list_sources(
        window_days=window,
        order=order,
        limit=limit,
        offset=offset,
        retention_days=settings.log_source_anomaly_retention_days,
    )
    return list_out(listing)


@router.get("/sources/{log_source_id}", response_model=NoiseSourceDetailOut)
async def get_noise_source(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> NoiseSourceDetailOut:
    """One source: both windows, the per-detector breakdown and its current knobs.

    Unlike the list this includes a source that is not monitored, because its
    page must say ``NOT_COLLECTED`` and null counts rather than 404 or zero.
    """
    detail = await NoiseReadModel(session, settings=settings).source_detail(
        log_source_id,
        retention_days=settings.log_source_anomaly_retention_days,
    )
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    return detail_out(detail)


@router.post("/sources/{log_source_id}/preview", response_model=NoisePreviewOut)
async def preview_noise_source(
    log_source_id: uuid.UUID,
    payload: NoisePreviewIn,
    session: SessionDep,
    settings: SettingsDep,
) -> NoisePreviewOut:
    """Replay 7 or 28 stored days under the current and proposed thresholds.

    A POST only because the proposed values are a body, like the custom-rule
    preview. Nothing here writes, audits or calls QRadar: it reads four
    statements and folds them through the production detectors. The caller
    needs the router's ``read:anomalies`` and no write permission.
    """
    proposed = payload.thresholds.model_dump(exclude_none=True, exclude_unset=True)
    result = await NoiseBacktest(session, settings=settings).preview(
        log_source_id,
        proposed=proposed,
        window_days=payload.window_days,
    )
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    return preview_out(result)
