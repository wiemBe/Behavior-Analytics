"""Admin reads of the stored QRadar health in detail (D-42): disk, system, pipeline, notifications.

``/admin/qradar-health/latest`` is the newest stored window of every enabled console: per host
the disk (fullest allowlisted mount and the others), system and pipeline figures, and QRadar's
own system notifications, each part with its state and reason. ``/admin/qradar-health/history``
is a bounded series per host (the fullest mount, the rule-engine queue, the throttle spill) and
the reachability and latency of the REST health samples, over 24 hours or 7 days.

Both read the database only and ask QRadar nothing. A window that was not collected says so;
there is never a zero in its place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep, SettingsDep
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.schemas.qradar_health_detail import HistoryOut, LatestOut
from app.services.qradar_health_detail import (
    HISTORY_HOURS,
    NOTIFICATION_HOURS_MAX,
    read_history,
    read_latest,
)

router = APIRouter(prefix="/admin/qradar-health", tags=["qradar-health"])

HOURS_CODE = "qradar_health_detail.hours.invalid"


@router.get("/latest", response_model=LatestOut)
async def qradar_health_latest(
    session: SessionDep,
    settings: SettingsDep,
    notifications_hours: Annotated[int | None, Query(ge=1, le=NOTIFICATION_HOURS_MAX)] = None,
) -> LatestOut:
    return await read_latest(session, settings, notifications_hours=notifications_hours)


@router.get("/history", response_model=HistoryOut)
async def qradar_health_history(
    session: SessionDep,
    settings: SettingsDep,
    hours: Annotated[int, Query()] = 24,
) -> HistoryOut:
    if hours not in HISTORY_HOURS:
        raise CodedHTTPException(status.HTTP_400_BAD_REQUEST, MessageRef(HOURS_CODE))
    return await read_history(session, settings, hours)
