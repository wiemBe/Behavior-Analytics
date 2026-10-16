"""Break-glass local administrator.

    python -m app.cli.users create-admin --username <name>

Creates that username, or resets it, as a local account holding the admin
role. The generated password is printed once, after the row is committed,
and ``must_change_password`` is set. The password is not logged.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import dispose_engine, get_sessionmaker
from app.models.enums import AuthSource
from app.models.identity import Role, User
from app.repositories.audit import record_audit
from app.security.passwords import hash_password
from app.security.usernames import USERNAME_SHAPE, normalize_username
from app.services.auth_sessions import revoke_user_sessions
from app.services.seed import ensure_default_roles


def _generate_password(username: str) -> str:
    while True:
        candidate = secrets.token_urlsafe(18)
        if len(candidate) >= 12 and candidate.casefold() != username.casefold():
            return candidate


async def _create_admin(raw_username: str) -> tuple[str, str]:
    username = normalize_username(raw_username)
    if username is None:
        raise ValueError(USERNAME_SHAPE)
    password = _generate_password(username)
    maker = get_sessionmaker()
    try:
        async with maker() as session:
            await ensure_default_roles(session)
            admin = await session.scalar(select(Role).where(Role.name == "admin"))
            if admin is None or "admin:*" not in (admin.permissions or []):
                raise ValueError("the admin role does not grant admin:*")
            user = await session.scalar(
                select(User)
                .where(User.username == username)
                .options(selectinload(User.roles))
            )
            if user is None:
                user = User(username=username, display_name=username, roles=[])
                session.add(user)
            # An existing directory account becomes local. The directory
            # password is not kept, because this row must not store one.
            user.auth_source = AuthSource.LOCAL.value
            user.hashed_password = hash_password(password)
            user.must_change_password = True
            user.is_active = True
            user.disabled_at = None
            user.failed_login_count = 0
            user.locked_until = None
            if admin not in user.roles:
                user.roles.append(admin)
            await session.flush()
            await revoke_user_sessions(session, user.id)
            await record_audit(
                session,
                action="user.break_glass",
                actor=username,
                actor_id=user.id,
                object_type="user",
                object_id=str(user.id),
                outcome="SUCCESS",
                detail={
                    "username": username,
                    "auth_source": AuthSource.LOCAL.value,
                    "must_change": True,
                },
            )
            await session.commit()
    finally:
        await dispose_engine()
    return username, password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.users")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin")
    create.add_argument("--username", required=True)
    args = parser.parse_args(argv)
    try:
        username, password = asyncio.run(_create_admin(args.username))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # Printed only after commit. stdout is the one place the password exists.
    print(f"username: {username}")
    print(f"password: {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
