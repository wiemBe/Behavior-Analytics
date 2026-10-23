"""Resolve the request principal.

Session cookies are required outside development and whenever ``auth_provider``
is ``session``. Development with ``auth_provider=local`` keeps the local-dev
principal so the existing suite does not need a directory. ``oidc`` in
development is unchanged. The session lookup uses its own short transaction,
so a route that does not authenticate never opens a database connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.models.identity import User
from app.security.rbac import Principal
from app.services.auth_sessions import (
    AuthenticationRequired,
    principal_from_cookie,
    touch_last_seen,
)

_LOCAL_DEV_PERMISSIONS = frozenset({"admin:*"})
_LOCAL_READONLY_PERMISSIONS = frozenset({"read:*"})
_MUST_CHANGE_DETAIL = "A password change is required before any other request."


def principal_for(user: User) -> Principal:
    """A principal whose permissions are the user's roles at this moment."""
    permissions = frozenset(
        permission for role in user.roles for permission in (role.permissions or [])
    )
    return Principal(
        subject=user.username or str(user.id),
        email=user.email,
        permissions=permissions,
        username=user.username,
        auth_source=user.auth_source,
        must_change_password=user.must_change_password,
        roles=tuple(sorted(role.name for role in user.roles)),
        user_id=user.id,
    )


def _allowed_while_password_must_change(method: str, path: str, prefix: str) -> bool:
    return (method.upper(), path) in {
        ("GET", f"{prefix}/me"),
        ("POST", f"{prefix}/auth/password"),
        ("POST", f"{prefix}/auth/logout"),
    }


@asynccontextmanager
async def _request_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Drive ``get_session``, including a test's dependency override.

    This is a separate transaction from the handler's session. It commits the
    last-seen update (or the removal of a dead session) before the handler
    runs, and it is not opened at all for the development principal.
    """
    factory = request.app.dependency_overrides.get(get_session, get_session)
    agen = factory()
    session = await agen.__anext__()
    try:
        yield session
    except Exception as exc:
        with suppress(StopAsyncIteration):
            await agen.athrow(exc)
        raise
    else:
        with suppress(StopAsyncIteration):
            await agen.__anext__()


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    qd_session: Annotated[str | None, Cookie()] = None,
    settings: Settings = Depends(get_settings),
) -> Principal:
    if settings.session_cookies_enforced():
        if settings.auth_provider == "oidc":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "OIDC is not available")
        return await _session_principal(request, qd_session, settings)
    if settings.auth_provider == "local":
        return _local_principal(authorization, settings)
    return await _oidc_principal(authorization, settings)


async def _session_principal(
    request: Request, token: str | None, settings: Settings
) -> Principal:
    try:
        async with _request_session(request) as session:
            user, row = await principal_from_cookie(session, token, settings)
            if user.must_change_password and not _allowed_while_password_must_change(
                request.method, request.url.path, settings.api_prefix
            ):
                raise HTTPException(status.HTTP_403_FORBIDDEN, _MUST_CHANGE_DETAIL)
            await touch_last_seen(session, row, datetime.now(UTC))
            return principal_for(user)
    except AuthenticationRequired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.detail) from exc


def _local_principal(authorization: str | None, settings: Settings) -> Principal:
    if settings.is_hardened:
        # A hardened environment takes the session branch before this. Kept so a
        # caller that reaches the local provider directly still fails closed.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "local auth is disabled outside development; configure AUTH_PROVIDER=session",
        )
    if not authorization:
        return Principal(
            subject="local-dev",
            email="dev@localhost",
            permissions=_LOCAL_DEV_PERMISSIONS,
            username="local-dev",
        )
    return Principal(
        subject="local-token",
        permissions=_LOCAL_READONLY_PERMISSIONS,
        username="local-token",
    )


async def _oidc_principal(authorization: str | None, settings: Settings) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    # OIDC stays out of scope. A bearer token is refused rather than trusted.
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "OIDC verification is not available",
    )
