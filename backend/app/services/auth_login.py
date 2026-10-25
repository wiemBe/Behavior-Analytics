"""Sign-in.

A local account is checked locally and never falls through to the directory.
Anything else is a directory bind when the directory is enabled, and a
refusal when it is not. The HTTP answer for a bad password, an unknown name,
a disabled account and a lock is the same sentence. Directory outage is the
one different answer, and it does not say the credentials were valid.

Failures are flushed here and committed by the route before it raises.
``get_session`` rolls back on ``HTTPException``, so the route cannot raise
until the lockout row and its audit record are committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.models.enums import AuthSource
from app.models.identity import User
from app.repositories.audit import record_audit
from app.security.passwords import (
    hash_password_async,
    password_policy_reasons,
    verify_password_async,
)
from app.security.sanitizer import sanitize_text
from app.security.usernames import normalize_username
from app.services.auth_sessions import (
    cleanup_expired,
    issue_session,
    revoke_user_sessions,
)
from app.services.ldap_bind import bind_directory
from app.services.ldap_config import get_ldap_row, spec_from_row

INVALID_CREDENTIALS = "Invalid username or password"
DIRECTORY_UNAVAILABLE = (
    "The directory server is unavailable. Sign-in was not completed."
)


@dataclass(frozen=True)
class LoginSuccess:
    token: str
    username: str
    auth_source: str
    must_change_password: bool
    roles: tuple[str, ...]


@dataclass(frozen=True)
class LoginFailure:
    status_code: int
    detail: str


def is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def clear_expired_lock(user: User, now: datetime) -> None:
    """A lock that has elapsed does not count against the next attempt."""
    if user.locked_until is not None and user.locked_until <= now:
        user.locked_until = None
        user.failed_login_count = 0


def apply_failure(
    user: User, now: datetime, *, threshold: int, lock_minutes: int
) -> bool:
    """Count one failure. Return True when this failure starts the lock."""
    user.failed_login_count += 1
    if user.failed_login_count >= threshold:
        user.locked_until = now + timedelta(minutes=lock_minutes)
        return True
    return False


def _disabled(user: User) -> bool:
    return user.disabled_at is not None or not user.is_active


async def authenticate(
    session: AsyncSession,
    settings: Settings,
    raw_username: str,
    password: str,
    *,
    source_ip: str | None,
    user_agent: str | None,
) -> LoginSuccess | LoginFailure:
    now = datetime.now(UTC)
    await cleanup_expired(session, settings, now)
    username = normalize_username(raw_username)
    if username is None:
        await verify_password_async(password, None)
        await _audit(
            session,
            "auth.login_failed",
            "<rejected>",
            reason="rejected_username",
            source_ip=source_ip,
            user_agent=user_agent,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)

    user = await session.scalar(
        select(User).where(User.username == username).options(selectinload(User.roles))
    )
    if password == "":
        return await _empty_password(
            session, settings, user, username, now, source_ip, user_agent
        )
    if user is not None and user.auth_source == AuthSource.LOCAL.value:
        return await _local(
            session, settings, user, password, now, source_ip, user_agent
        )
    return await _directory(
        session, settings, user, username, password, now, source_ip, user_agent
    )


async def _empty_password(
    session: AsyncSession,
    settings: Settings,
    user: User | None,
    username: str,
    now: datetime,
    source_ip: str | None,
    user_agent: str | None,
) -> LoginFailure:
    """An empty password is never a bind and never a successful local check."""
    stored = user.hashed_password if user is not None else None
    await verify_password_async("", stored)
    if user is not None and is_locked(user, now):
        await _audit(
            session, "auth.locked", username, reason="locked",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)
    if user is not None:
        await _count_failure(
            session, settings, user, username, now, "empty_password",
            source_ip, user_agent,
        )
    else:
        await _audit(
            session, "auth.login_failed", username, reason="empty_password",
            source_ip=source_ip, user_agent=user_agent,
        )
    return LoginFailure(401, INVALID_CREDENTIALS)


async def _local(
    session: AsyncSession,
    settings: Settings,
    user: User,
    password: str,
    now: datetime,
    source_ip: str | None,
    user_agent: str | None,
) -> LoginSuccess | LoginFailure:
    username = user.username or ""
    if is_locked(user, now):
        # The result is ignored. The check keeps the response time close to a
        # real attempt, and the lock window is not extended.
        await verify_password_async(password, user.hashed_password)
        await _audit(
            session, "auth.locked", username, reason="locked",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)
    clear_expired_lock(user, now)
    if not await verify_password_async(password, user.hashed_password):
        await _count_failure(
            session, settings, user, username, now, "invalid_credentials",
            source_ip, user_agent,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)
    if _disabled(user):
        await _audit(
            session, "auth.login_failed", username, reason="disabled",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)
    return await _success(session, settings, user, now, source_ip, user_agent)


async def _directory(
    session: AsyncSession,
    settings: Settings,
    user: User | None,
    username: str,
    password: str,
    now: datetime,
    source_ip: str | None,
    user_agent: str | None,
) -> LoginSuccess | LoginFailure:
    if user is not None and is_locked(user, now):
        await _audit(
            session, "auth.locked", username, reason="locked",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)
    if user is not None:
        clear_expired_lock(user, now)

    row = await get_ldap_row(session)
    spec = spec_from_row(row) if row is not None and row.enabled else None
    if spec is None:
        await verify_password_async(password, None)
        await _audit(
            session, "auth.login_failed", username, reason="ldap_disabled",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)

    try:
        report = await bind_directory(spec, username, password)
    except Exception as exc:
        await _audit(
            session, "auth.login_failed", username, reason="directory_unavailable",
            source_ip=source_ip, user_agent=user_agent, user=user,
            extra={"directory_error": type(exc).__name__},
        )
        return LoginFailure(503, DIRECTORY_UNAVAILABLE)

    if report.invalid_credentials:
        if user is not None:
            await _count_failure(
                session, settings, user, username, now, "invalid_credentials",
                source_ip, user_agent,
            )
        else:
            await _audit(
                session, "auth.login_failed", username, reason="invalid_credentials",
                source_ip=source_ip, user_agent=user_agent,
            )
        return LoginFailure(401, INVALID_CREDENTIALS)

    if not report.ok:
        await _audit(
            session, "auth.login_failed", username, reason="directory_unavailable",
            source_ip=source_ip, user_agent=user_agent, user=user,
            extra={"directory_error": report.bind_reason or report.tls_reason},
        )
        return LoginFailure(503, DIRECTORY_UNAVAILABLE)

    created = False
    if user is None:
        user = User(
            username=username,
            auth_source=AuthSource.LDAP.value,
            display_name=username,
            is_active=True,
            must_change_password=False,
            failed_login_count=0,
        )
        session.add(user)
        await session.flush()
        created = True
        await record_audit(
            session,
            action="user.provision",
            actor=username,
            actor_id=user.id,
            object_type="user",
            object_id=str(user.id),
            outcome="SUCCESS",
            detail={"username": username, "auth_source": AuthSource.LDAP.value},
            source_ip=source_ip,
            user_agent=user_agent,
        )
    if _disabled(user):
        await _audit(
            session, "auth.login_failed", username, reason="disabled",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )
        return LoginFailure(401, INVALID_CREDENTIALS)
    await _apply_profile(session, user, report.display_name, report.mail)
    if created:
        await session.refresh(user, attribute_names=["roles"])
    return await _success(session, settings, user, now, source_ip, user_agent)


async def _success(
    session: AsyncSession,
    settings: Settings,
    user: User,
    now: datetime,
    source_ip: str | None,
    user_agent: str | None,
    *,
    record_login: bool = True,
) -> LoginSuccess:
    username = user.username or ""
    user.failed_login_count = 0
    user.locked_until = None
    if record_login:
        user.last_login_at = now
    token = await issue_session(session, user, settings, now)
    if record_login:
        await record_audit(
            session,
            action="auth.login",
            actor=username,
            actor_id=user.id,
            object_type="user",
            object_id=str(user.id),
            outcome="SUCCESS",
            detail={"username": username, "auth_source": user.auth_source},
            source_ip=source_ip,
            user_agent=user_agent,
        )
    return LoginSuccess(
        token=token,
        username=username,
        auth_source=user.auth_source,
        must_change_password=user.must_change_password,
        roles=tuple(sorted(role.name for role in user.roles)),
    )


async def _count_failure(
    session: AsyncSession,
    settings: Settings,
    user: User,
    username: str,
    now: datetime,
    reason: str,
    source_ip: str | None,
    user_agent: str | None,
) -> None:
    locked_now = apply_failure(
        user,
        now,
        threshold=settings.login_lockout_threshold,
        lock_minutes=settings.login_lockout_minutes,
    )
    await _audit(
        session, "auth.login_failed", username, reason=reason,
        source_ip=source_ip, user_agent=user_agent, user=user,
    )
    if locked_now:
        await _audit(
            session, "auth.locked", username, reason="locked",
            source_ip=source_ip, user_agent=user_agent, user=user,
        )


async def _audit(
    session: AsyncSession,
    action: str,
    actor: str,
    *,
    reason: str,
    source_ip: str | None,
    user_agent: str | None,
    user: User | None = None,
    extra: dict[str, object] | None = None,
) -> None:
    detail: dict[str, object] = {"username": actor, "reason": reason}
    if extra:
        detail.update(extra)
    await record_audit(
        session,
        action=action,
        actor=actor,
        actor_id=user.id if user is not None else None,
        object_type="user" if user is not None else None,
        object_id=str(user.id) if user is not None else None,
        outcome="FAILURE",
        detail=detail,
        source_ip=source_ip,
        user_agent=user_agent,
    )


async def _apply_profile(
    session: AsyncSession, user: User, display_name: str | None, mail: str | None
) -> None:
    cleaned_display = _clean_display(display_name)
    if cleaned_display:
        user.display_name = cleaned_display
    cleaned_mail = _clean_mail(mail)
    if not cleaned_mail or cleaned_mail == user.email:
        return
    other = await session.scalar(
        select(User.id).where(User.email == cleaned_mail, User.id != user.id)
    )
    if other is None:
        user.email = cleaned_mail


async def change_own_password(
    session: AsyncSession,
    settings: Settings,
    user: User,
    current_password: str,
    new_password: str,
    *,
    source_ip: str | None,
    user_agent: str | None,
) -> LoginSuccess:
    """Replace a local password and issue a new session.

    A wrong current password does not count toward the login lock. The new
    password is never written to the audit record.
    """
    if user.auth_source != AuthSource.LOCAL.value or not user.username:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "LDAP accounts change their password in the directory, not here.",
        )
    if not await verify_password_async(current_password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "current password is wrong")
    reasons = password_policy_reasons(new_password, user.username)
    if reasons:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, reasons)
    now = datetime.now(UTC)
    user.hashed_password = await hash_password_async(new_password)
    user.must_change_password = False
    await revoke_user_sessions(session, user.id)
    await record_audit(
        session,
        action="auth.password.change",
        actor=user.username,
        actor_id=user.id,
        object_type="user",
        object_id=str(user.id),
        outcome="SUCCESS",
        detail={"username": user.username, "must_change": False},
        source_ip=source_ip,
        user_agent=user_agent,
    )
    return await _success(
        session, settings, user, now, source_ip, user_agent, record_login=False
    )


async def change_ui_language(
    session: AsyncSession,
    user: User,
    ui_language: str,
    *,
    source_ip: str | None,
    user_agent: str | None,
) -> bool:
    """Set the account's interface language. Return True when it changed.

    Writing the value the row already holds is a no-op and writes no audit
    record: the account's state did not change, so its trail should not claim
    one did.
    """
    if user.ui_language == ui_language:
        return False
    old = user.ui_language
    user.ui_language = ui_language
    await record_audit(
        session,
        action="account.preferences.update",
        actor=user.username or str(user.id),
        actor_id=user.id,
        object_type="user",
        object_id=str(user.id),
        outcome="SUCCESS",
        detail={"username": user.username, "old": old, "new": ui_language},
        source_ip=source_ip,
        user_agent=user_agent,
    )
    return True


def _clean_display(value: str | None) -> str | None:
    cleaned = sanitize_text(value)
    if not cleaned:
        return None
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    return cleaned[:255]


def _clean_mail(value: str | None) -> str | None:
    cleaned = sanitize_text(value)
    if not cleaned:
        return None
    cleaned = cleaned.strip()
    if len(cleaned) > 255 or cleaned.count("@") != 1:
        return None
    local, _, domain = cleaned.partition("@")
    if not local or not domain or any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        return None
    return cleaned
