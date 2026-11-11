"""The SOC report summary (SOC-8b): `GET /reports/summary`.

Principal-only, like `/queue` (`queue.py:3-5`): it is not behind one router-level permission,
because that would make the report all or nothing. Each block checks its own read permission
and reports `FORBIDDEN`, naming it, when the caller lacks it. Nothing is stored, and nothing
calls QRadar.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, status

from app.api.deps import SessionDep, SettingsDep
from app.api.routes.anomalies import PrincipalDep
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.schemas.reports import ReportSummaryOut
from app.services.reports import PeriodError, build_summary, resolve_period

router = APIRouter(prefix="/reports", tags=["reports"])

_PARAMS = frozenset({"period", "start"})


def _refuse(ref: MessageRef) -> CodedHTTPException:
    return CodedHTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, ref)


@router.get("/summary", response_model=ReportSummaryOut)
async def get_summary(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    period: str = Query(default="last_7d"),
    start: str | None = Query(default=None),
) -> ReportSummaryOut:
    """Alerts, anomalies, anomaly clocks and noise by detector for one UTC period.

    `start` is read only for the `calendar_*` periods and is ignored for `last_*`.
    """
    for name in request.query_params:
        if name not in _PARAMS:
            raise _refuse(MessageRef("reports.param_unknown", {"name": name[:64]}))
    now = datetime.now(UTC)
    begin: datetime | None = None
    if start is not None and not period.startswith("last_"):
        try:
            begin = datetime.fromisoformat(start)
        except ValueError:
            raise _refuse(MessageRef("reports.start_not_boundary")) from None
    try:
        kind, begin, end = resolve_period(period, begin, now)
    except PeriodError as exc:
        raise _refuse(exc.ref) from exc
    return await build_summary(
        session, settings, principal, period=kind, start=begin, end=end, now=now
    )
