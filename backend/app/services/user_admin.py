"""Account administration.

Every write records a before and after snapshot with no password material.
The last active user whose roles include ``admin:*`` cannot be disabled and
cannot lose that role. A legacy row with no username, and a local row with
no password, are not counted: neither can sign in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.enums import AuthSource
from app.models.identity import Role, User
from app.repositories.audit import record_audit
from app.security.passwords import hash_password_async, password_policy_refs
from app.security.rbac import PERM_ADMIN
from app.security.sanitizer import sanitize_text
from app.security.usernames import normalize_username
from app.services.auth_sessions import revoke_user_sessions


@dataclass(frozen=True)
class Actor:
    username: str
    user_id: uuid.UUID | None
    source_ip: str | None
    user_agent: str | None


def holds_admin(user: User) -> bool:
    return any(PERM_ADMIN in (role.permissions or []) for role in user.roles)


def actionable(user: User) -> bool:
    """Someone who can sign in and whose removal would matter."""
    if user.disabled_at is not None or not user.is_active:
        return False
    if not user.username:
        return False
    return not (user.auth_source == AuthSource.LOCAL.value and not user.hashed_password)


def user_snapshot(user: User) -> dict[str, object]:
    locked = user.locked_until.isoformat() if user.locked_until is not None else None
    return {
        "username": user.username,
        "auth_source": user.auth_source,
        "display_name": user.display_name,
        "email": user.email,
        "roles": sorted(role.name for role in user.roles),
        "disabled": user.disabled_at is not None or not user.is_active,
        # ``must_change`` avoids the redactor, which masks any key containing "password".
        "must_change": user.must_change_password,
        "locked_until": locked,
    }


async def list_users(session: AsyncSession) -> list[User]:
    rows = await session.scalars(
        select(User).options(selectinload(User.roles)).order_by(User.username)
    )
    return list(rows)


async def create_user(
    session: AsyncSession,
    actor: Actor,
    *,
    username: str,
    auth_source: str,
    password: str | None,
    email: str | None,
    display_name: str | None,
    role_names: list[str],
) -> User:
    name = normalize_username(username)
    if name is None:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, MessageRef("auth.username.shape")
        )
    address = _email(email)
    shown = _display(display_name)
    roles = await _roles(session, role_names)
    if auth_source == AuthSource.LDAP.value:
        if password:
            raise CodedHTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                MessageRef("auth.user.ldap_no_password"),
            )
        hashed = None
        must_change = False
    elif auth_source == AuthSource.LOCAL.value:
        if not password:
            raise CodedHTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                MessageRef("auth.user.local_needs_password"),
            )
        reasons = password_policy_refs(password, name)
        if reasons:
            raise CodedHTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, refs=reasons)
        hashed = await hash_password_async(password)
        must_change = True
    else:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, MessageRef("auth.user.source_invalid")
        )

    await _reject_duplicates(session, name, address)
    user = User(
        username=name,
        auth_source=auth_source,
        hashed_password=hashed,
        email=address,
        display_name=shown,
        is_active=True,
        must_change_password=must_change,
        failed_login_count=0,
        roles=roles,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise CodedHTTPException(
            status.HTTP_409_CONFLICT, MessageRef("auth.user.exists")
        ) from exc
    await _wrote(session, actor, "user.create", user, before=None, after=user_snapshot(user))
    return user


async def set_roles(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID, role_names: list[str]
) -> User:
    user = await _locked(session, user_id)
    roles = await _roles(session, role_names)
    keeps = any(PERM_ADMIN in (role.permissions or []) for role in roles)
    await _keep_last_admin(session, user, target_keeps_admin=keeps)
    before = user_snapshot(user)
    user.roles = roles
    await session.flush()
    await _wrote(
        session, actor, "user.roles.update", user, before=before, after=user_snapshot(user)
    )
    return user


async def set_disabled(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID, *, disabled: bool
) -> User:
    user = await _locked(session, user_id)
    if disabled:
        await _keep_last_admin(session, user, target_keeps_admin=False)
    before = user_snapshot(user)
    if disabled:
        user.disabled_at = datetime.now(UTC)
        user.is_active = False
        await revoke_user_sessions(session, user.id)
        action = "user.disable"
    else:
        user.disabled_at = None
        user.is_active = True
        action = "user.enable"
    await session.flush()
    await _wrote(session, actor, action, user, before=before, after=user_snapshot(user))
    return user


async def unlock_user(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID
) -> User:
    user = await _locked(session, user_id)
    before = user_snapshot(user)
    user.locked_until = None
    user.failed_login_count = 0
    await session.flush()
    await _wrote(
        session, actor, "user.unlock", user, before=before, after=user_snapshot(user)
    )
    return user


async def reset_password(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID, password: str
) -> User:
    """Set a local password. The password is not returned and not audited."""
    user = await _locked(session, user_id)
    if user.auth_source != AuthSource.LOCAL.value or not user.username:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, MessageRef("auth.user.reset_local_only")
        )
    reasons = password_policy_refs(password, user.username)
    if reasons:
        raise CodedHTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, refs=reasons)
    before = user_snapshot(user)
    user.hashed_password = await hash_password_async(password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    await revoke_user_sessions(session, user.id)
    await session.flush()
    await _wrote(
        session, actor, "user.password.reset", user, before=before, after=user_snapshot(user)
    )
    return user


async def _locked(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.roles))
        .with_for_update()
    )
    if user is None:
        raise CodedHTTPException(status.HTTP_404_NOT_FOUND, MessageRef("auth.user.not_found"))
    return user


async def _keep_last_admin(
    session: AsyncSession, target: User, *, target_keeps_admin: bool
) -> None:
    if not actionable(target) or not holds_admin(target) or target_keeps_admin:
        return
    others = await list_users(session)
    remaining = [
        user
        for user in others
        if user.id != target.id and actionable(user) and holds_admin(user)
    ]
    if not remaining:
        raise CodedHTTPException(status.HTTP_409_CONFLICT, MessageRef("auth.user.last_admin"))


async def _roles(session: AsyncSession, names: list[str]) -> list[Role]:
    ordered = list(dict.fromkeys(names))
    if not ordered:
        return []
    found = {
        role.name: role
        for role in await session.scalars(select(Role).where(Role.name.in_(ordered)))
    }
    missing = [name for name in ordered if name not in found]
    if missing:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            refs=[MessageRef("auth.user.role_unknown", {"name": name}) for name in missing],
        )
    return [found[name] for name in ordered]


async def _reject_duplicates(
    session: AsyncSession, username: str, email: str | None
) -> None:
    existing = await session.scalar(select(User.id).where(User.username == username))
    if existing is not None:
        raise CodedHTTPException(
            status.HTTP_409_CONFLICT, MessageRef("auth.user.username_taken")
        )
    if email is None:
        return
    taken = await session.scalar(select(User.id).where(User.email == email))
    if taken is not None:
        raise CodedHTTPException(
            status.HTTP_409_CONFLICT, MessageRef("auth.user.email_taken")
        )


def _email(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    email = value.strip()
    if (
        len(email) > 255
        or email.count("@") != 1
        or any(ord(ch) < 32 or ch.isspace() for ch in email)
    ):
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, MessageRef("auth.user.email_unusable")
        )
    local, _, domain = email.partition("@")
    if not local or not domain:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, MessageRef("auth.user.email_unusable")
        )
    return email


def _display(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = sanitize_text(value)
    if not cleaned:
        return None
    cleaned = cleaned.strip()
    return cleaned[:255] if cleaned else None


async def _wrote(
    session: AsyncSession,
    actor: Actor,
    action: str,
    user: User,
    *,
    before: dict[str, object] | None,
    after: dict[str, object],
) -> None:
    await record_audit(
        session,
        action=action,
        actor=actor.username,
        actor_id=actor.user_id,
        object_type="user",
        object_id=str(user.id),
        outcome="SUCCESS",
        detail={"before": before, "after": after},
        source_ip=actor.source_ip,
        user_agent=actor.user_agent,
    )
