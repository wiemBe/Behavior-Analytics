"""Account administration. Guarded by ``admin:users``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from app.api.deps import SessionDep, requires
from app.api.request_meta import client_ip, user_agent
from app.models.identity import Role, User
from app.schemas.users import PasswordResetIn, RoleOut, RolesIn, UserCreate, UserOut
from app.security.auth import get_principal
from app.security.rbac import PERM_USER_ADMIN, Principal
from app.services.user_admin import (
    Actor,
    create_user,
    list_users,
    reset_password,
    set_disabled,
    set_roles,
    unlock_user,
)

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(requires(PERM_USER_ADMIN))],
)


def _actor(request: Request, principal: Principal) -> Actor:
    return Actor(
        username=principal.username or principal.subject,
        user_id=principal.user_id,
        source_ip=client_ip(request),
        user_agent=user_agent(request),
    )


def _out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        auth_source=user.auth_source,
        display_name=user.display_name,
        email=user.email,
        roles=sorted(role.name for role in user.roles),
        disabled=user.disabled_at is not None or not user.is_active,
        disabled_at=user.disabled_at,
        last_login_at=user.last_login_at,
        locked_until=user.locked_until,
        must_change_password=user.must_change_password,
    )


@router.get("", response_model=list[UserOut])
async def get_users(session: SessionDep) -> list[UserOut]:
    return [_out(user) for user in await list_users(session)]


@router.get("/roles", response_model=list[RoleOut])
async def get_roles(session: SessionDep) -> list[RoleOut]:
    """Every role's name and permissions, in one read.

    The administration picker lists whatever this returns. A role added in the
    database (for example a detection engineer) appears without a frontend change.
    """
    rows = await session.execute(select(Role.name, Role.permissions).order_by(Role.name))
    return [RoleOut(name=name, permissions=list(permissions or [])) for name, permissions in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def post_user(
    request: Request,
    body: UserCreate,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> UserOut:
    user = await create_user(
        session,
        _actor(request, principal),
        username=body.username,
        auth_source=body.auth_source,
        password=body.password,
        email=body.email,
        display_name=body.display_name,
        role_names=body.roles,
    )
    return _out(user)


@router.put("/{user_id}/roles", response_model=UserOut)
async def put_roles(
    user_id: uuid.UUID,
    body: RolesIn,
    request: Request,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> UserOut:
    user = await set_roles(session, _actor(request, principal), user_id, body.roles)
    return _out(user)


@router.post("/{user_id}/disable", response_model=UserOut)
async def post_disable(
    user_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> UserOut:
    user = await set_disabled(
        session, _actor(request, principal), user_id, disabled=True
    )
    return _out(user)


@router.post("/{user_id}/enable", response_model=UserOut)
async def post_enable(
    user_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> UserOut:
    user = await set_disabled(
        session, _actor(request, principal), user_id, disabled=False
    )
    return _out(user)


@router.post("/{user_id}/unlock", response_model=UserOut)
async def post_unlock(
    user_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> UserOut:
    user = await unlock_user(session, _actor(request, principal), user_id)
    return _out(user)


@router.post("/{user_id}/reset-password", response_model=UserOut)
async def post_reset_password(
    user_id: uuid.UUID,
    body: PasswordResetIn,
    request: Request,
    session: SessionDep,
    principal: Principal = Depends(get_principal),
) -> UserOut:
    user = await reset_password(
        session, _actor(request, principal), user_id, body.password
    )
    return _out(user)
