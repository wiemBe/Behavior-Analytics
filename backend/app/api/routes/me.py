"""Caller identity, permissions, and own-account preferences."""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import SessionDep, get_principal
from app.api.request_meta import client_ip, user_agent
from app.models.identity import User
from app.schemas.me import MeOut, PreferencesIn
from app.security import rbac
from app.security.auth import principal_for
from app.security.rbac import Principal
from app.services.auth_login import change_ui_language

router = APIRouter(prefix="/me", tags=["me"])


def _effective_permissions(principal: Principal) -> list[str]:
    """Return every non-wildcard PERM_* constant that the principal has.

    The constants are collected dynamically from the rbac module so the list
    cannot drift. Any PERM_* value ending with ":*" is excluded because it is
    a wildcard, not a capability.
    """
    effective: list[str] = []
    for name in dir(rbac):
        if name.startswith("PERM_"):
            value = getattr(rbac, name)
            if isinstance(value, str) and not value.endswith(":*") and principal.has(value):
                effective.append(value)
    effective.sort()
    return effective


def _me_out(principal: Principal) -> MeOut:
    """The caller's answer, shared by GET /me and PUT /me/preferences."""
    # The column's check constraint already pins ui_language to the Literal's
    # two values; the cast is for the type checker, pydantic still validates.
    return MeOut(
        subject=principal.subject,
        email=principal.email,
        permissions=sorted(principal.permissions),
        effective_permissions=_effective_permissions(principal),
        username=principal.username,
        auth_source=principal.auth_source,
        must_change_password=principal.must_change_password,
        roles=list(principal.roles),
        ui_language=cast(Literal["en", "tr"], principal.ui_language),
    )


@router.get("", response_model=MeOut)
async def get_me(principal: Principal = Depends(get_principal)) -> MeOut:
    """Return the authenticated caller's identity and permissions.

    No additional permission is required: the endpoint returns only the
    caller's own subject, email and granted permissions, plus the derived
    effective permissions. Nothing secret is exposed.
    """
    return _me_out(principal)


@router.put("/preferences", response_model=MeOut)
async def update_preferences(
    request: Request,
    body: PreferencesIn,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> MeOut:
    """Store the caller's interface language on their own account.

    Any signed-in caller may use it: like GET /me it touches only the
    caller's own row, so no permission beyond a session applies. A principal
    without an account (a development principal) has nothing to store the
    choice on, and nothing is written. Re-sending the value the row already
    holds is a 200 that writes no audit record.
    """
    if principal.user_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This session has no account to store a preference on.",
        )
    user = await session.scalar(
        select(User)
        .where(User.id == principal.user_id)
        .options(selectinload(User.roles))
    )
    if user is None or user.disabled_at is not None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required.")
    await change_ui_language(
        session,
        user,
        body.ui_language,
        source_ip=client_ip(request),
        user_agent=user_agent(request),
    )
    return _me_out(principal_for(user))
