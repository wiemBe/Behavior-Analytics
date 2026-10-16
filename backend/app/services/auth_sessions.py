"""Server-side sessions.

The cookie carries a random 256-bit token. The row stores only its SHA-256
hex digest. Permissions are not stored here: they are read from the user's
roles on every request, so a role change or a disable applies immediately.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.models.identity import AuthSession, User

COOKIE_NAME = "qd_session"
_MAX_COOKIE = 512
_TOUCH_AFTER = timedelta(seconds=60)


class AuthenticationRequired(Exception):
    """No FastAPI import: the route layer turns this into HTTP 401."""

    def __init__(self, detail: str = "Authentication required.") -> None:
        self.detail = detail
        super().__init__(detail)


def new_token() -> tuple[str, str]:
    """Return ``(cookie value, sha256 hex)``. The cookie value is not stored."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expired(
    *,
    created_at: datetime,
    last_seen_at: datetime,
    expires_at: datetime,
    now: datetime,
    idle_minutes: int,
    max_hours: int,
) -> bool:
    """True at the boundary as well as past it.

    ``expires_at`` is the absolute lifetime stored when the session was
    issued. ``max_hours`` is the current setting, so tightening it expires a
    session on the next request without rewriting the row.
    """
    if now >= expires_at:
        return True
    if now >= created_at + timedelta(hours=max_hours):
        return True
    return now - last_seen_at >= timedelta(minutes=idle_minutes)


def cookie_kwargs(settings: Settings) -> dict[str, object]:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "strict",
        "secure": settings.is_production,
        "path": "/",
        "max_age": settings.session_max_hours * 3600,
    }


async def cleanup_expired(
    session: AsyncSession, settings: Settings, now: datetime
) -> None:
    """Delete a bounded batch of expired or idle sessions. Called from login."""
    idle_before = now - timedelta(minutes=settings.session_idle_minutes)
    ids = list(
        await session.scalars(
            select(AuthSession.id)
            .where(
                or_(
                    AuthSession.expires_at <= now,
                    AuthSession.last_seen_at <= idle_before,
                )
            )
            .limit(settings.session_cleanup_batch)
        )
    )
    if ids:
        await session.execute(delete(AuthSession).where(AuthSession.id.in_(ids)))


async def issue_session(
    session: AsyncSession, user: User, settings: Settings, now: datetime
) -> str:
    """Persist a new session and return the cookie value."""
    token, digest = new_token()
    session.add(
        AuthSession(
            token_hash=digest,
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=settings.session_max_hours),
        )
    )
    await session.flush()
    return token


async def delete_by_token(session: AsyncSession, token: str | None) -> None:
    if not token or len(token) > _MAX_COOKIE:
        return
    await session.execute(
        delete(AuthSession).where(AuthSession.token_hash == token_hash(token))
    )


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


async def touch_last_seen(session: AsyncSession, row: AuthSession, now: datetime) -> None:
    """Move last_seen_at, at most once a minute, and only after the caller is allowed."""
    if now - row.last_seen_at >= _TOUCH_AFTER:
        row.last_seen_at = now
        await session.flush()


async def principal_from_cookie(
    session: AsyncSession, token: str | None, settings: Settings
) -> tuple[User, AuthSession]:
    """Load the user behind a cookie, or raise :class:`AuthenticationRequired`.

    The caller updates ``last_seen_at`` only after the must-change gate, so a
    refused request does not keep an idle session alive.
    """
    if not token or len(token) > _MAX_COOKIE:
        raise AuthenticationRequired()
    row = await session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(token))
    )
    if row is None:
        raise AuthenticationRequired()
    now = datetime.now(UTC)
    if session_expired(
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        now=now,
        idle_minutes=settings.session_idle_minutes,
        max_hours=settings.session_max_hours,
    ):
        await session.delete(row)
        await session.flush()
        raise AuthenticationRequired()
    user = await session.scalar(
        select(User).where(User.id == row.user_id).options(selectinload(User.roles))
    )
    if user is None or user.disabled_at is not None or not user.is_active:
        await session.delete(row)
        await session.flush()
        raise AuthenticationRequired()
    return user, row
