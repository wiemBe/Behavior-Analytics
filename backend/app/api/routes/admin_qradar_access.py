"""QRadar access administration (ACC-1): which APIs the token can reach, and check again.

Every route needs ``admin:*``, applied at the router mount so a route added here cannot ship
unguarded. ``GET`` reads the stored results, one query for every console, and sends nothing to
QRadar. ``POST /check`` queues the check and answers 202 with the stored shape: it never probes
inside the request (a check outlasts the proxy's timeout). It is rate limited
(``RATE_LIMIT_ACCESS_CHECK``, counted also when a run is already going) and audited. The page
polls ``GET`` for ``check_running``. An API that became reachable starts its syncs.

Read-only against QRadar (repository rule 1), and no response body is stored or returned.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api.deps import SessionDep, SettingsDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.models.instance import QRadarInstance
from app.repositories.audit import record_audit
from app.schemas.qradar_access import AccessCheckOut, QRadarAccessOut
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.qradar_access import claim_run, finish_run, read_access

logger = logging.getLogger("app.api.qradar_access")

router = APIRouter(prefix="/admin/qradar-access", tags=["admin-qradar-access"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _check_limit() -> str:
    """The tighter limit of the check-now action, read per request."""
    return get_settings().rate_limit_access_check


async def _enabled_consoles(session: SessionDep) -> list[QRadarInstance]:
    rows = await session.scalars(
        select(QRadarInstance)
        .where(QRadarInstance.enabled.is_(True))
        .order_by(QRadarInstance.name)
    )
    return list(rows.all())


@router.get("", response_model=QRadarAccessOut)
async def get_access(session: SessionDep, settings: SettingsDep) -> QRadarAccessOut:
    """The latest stored result per console and catalog entry. Sends nothing to QRadar."""
    consoles = await _enabled_consoles(session)
    return QRadarAccessOut(
        consoles=await read_access(session, consoles),
        check_interval_seconds=settings.qradar_access_check_interval_seconds,
    )


@router.post("/check", response_model=AccessCheckOut, status_code=202)
@limiter.limit(_check_limit)
async def check_now(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> AccessCheckOut:
    """Queue a check of every enabled REST console and answer at once (202).

    The check itself runs in a worker: a console takes about a minute, longer than the proxy
    waits. Each console is marked as running here, before the task is queued, so a second request
    while one runs queues nothing and names the console under ``already_running``. Poll ``GET``
    for ``check_running`` and the results. A console whose task the queue refused is named under
    ``failed`` and its run is released.
    """
    from app.workers import tasks

    consoles = await _enabled_consoles(session)
    started: list[str] = []
    already_running: list[str] = []
    failed: list[str] = []
    for instance in consoles:
        if instance.provider_kind != "rest":
            continue
        name, instance_id = instance.name, instance.id
        if not await claim_run(session, instance_id):
            already_running.append(name)
            continue
        try:
            tasks.check_qradar_access_task.apply_async(args=[name, True])
        except Exception as exc:
            logger.warning(
                "access check was not queued",
                extra={"instance": name, "error_class": type(exc).__name__},
            )
            await finish_run(session, instance_id)
            failed.append(name)
            continue
        started.append(name)
    await record_audit(
        session,
        action="qradar_access.check",
        actor=principal.subject,
        object_type="qradar_access",
        outcome="FAILURE" if failed else "SUCCESS",
        detail={
            "consoles": len(consoles),
            "started": started,
            "already_running": already_running,
            "failed": failed,
        },
    )
    return AccessCheckOut(
        consoles=await read_access(session, consoles),
        check_interval_seconds=settings.qradar_access_check_interval_seconds,
        started=started,
        already_running=already_running,
        failed=failed,
    )
