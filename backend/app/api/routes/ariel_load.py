"""Admin reads of how much Ariel load the platform put on QRadar.

``/ariel-load`` summarises a window per caller: hours, default 24, at most 30
days. An empty window says so. Percentiles (wall and execution) are over
COMPLETED searches only; sums are the load itself and cover every finished
search, a TIMEOUT's partial execution time included. A sum is null, never 0,
when no search in it reported the figure, and ``*_unknown`` counts the ones
that did not.

``/ariel-load/current`` is one cheap read of the load now: searches in flight,
the permit slots, and the last 5, 15 and 60 minutes. A part that cannot be read
says so. The bodies have no AQL and no result rows.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from app.api.deps import SessionDep
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.services.ariel_search_log import HOURS_CODE, load_current, load_summary, parse_hours

router = APIRouter(prefix="/admin", tags=["ariel-load"])


@router.get("/ariel-load")
async def ariel_load(
    session: SessionDep,
    hours: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    try:
        window = parse_hours(hours)
    except ValueError as exc:
        raise CodedHTTPException(status.HTTP_400_BAD_REQUEST, MessageRef(HOURS_CODE)) from exc
    return await load_summary(session, hours=window)


@router.get("/ariel-load/current")
async def ariel_load_current(session: SessionDep) -> dict[str, Any]:
    return await load_current(session)
