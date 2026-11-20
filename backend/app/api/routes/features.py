"""Operator-owned feature switches."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool

from app.api.deps import SessionDep, SettingsDep, requires
from app.api.request_meta import client_ip
from app.security.rbac import PERM_FEATURES_ADMIN, PERM_FEATURES_READ, Principal
from app.services.feature_management import FeatureError, change
from app.services.feature_overview import overview
from app.services.feature_switches import invalidate_effective_settings

router = APIRouter(prefix="/admin/features", tags=["features"])
Reader = Annotated[Principal, Depends(requires(PERM_FEATURES_READ))]
Writer = Annotated[Principal, Depends(requires(PERM_FEATURES_ADMIN))]


class SwitchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


@router.get("")
async def read_features(session: SessionDep, settings: SettingsDep, principal: Reader) -> dict:
    return await overview(session, settings, principal)


async def _write(session: SessionDep, settings: SettingsDep, key: str, enabled: bool | None,
                 principal: Principal, request: Request) -> None:
    try:
        await change(session, settings, {key: enabled}, actor=principal.subject,
                     source_ip=client_ip(request))
    except FeatureError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "problems": exc.problems,
                                         "message": "; ".join(exc.problems)}) from exc
    # Commit first, then drop the cache: a read between the two would cache the old value.
    await session.commit()
    invalidate_effective_settings()


@router.put("/{key}", status_code=204)
async def set_feature(key: str, payload: SwitchUpdate, request: Request, session: SessionDep,
                      settings: SettingsDep, principal: Writer) -> Response:
    await _write(session, settings, key, payload.enabled, principal, request)
    return Response(status_code=204)


@router.delete("/{key}", status_code=204)
async def reset_feature(key: str, request: Request, session: SessionDep,
                        settings: SettingsDep, principal: Writer) -> Response:
    await _write(session, settings, key, None, principal, request)
    return Response(status_code=204)
