"""Password hashing for local accounts.

The ``bcrypt`` package is used directly. passlib is not on this path: its
bcrypt backend is unmaintained, and this process already depends on
``bcrypt`` itself. The cost is 12, the floor this deployment accepts.
"""

from __future__ import annotations

import asyncio
import os
import threading

import bcrypt

BCRYPT_ROUNDS = 12
_MAX_PASSWORD_BYTES = 72

_dummy_lock = threading.Lock()
_dummy_hash: bytes | None = None


def _dummy() -> bytes:
    """One cost-12 hash, built once, so an unknown user takes the same work."""
    global _dummy_hash
    if _dummy_hash is None:
        with _dummy_lock:
            if _dummy_hash is None:
                _dummy_hash = bcrypt.hashpw(os.urandom(16), bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
    return _dummy_hash


def hash_password(password: str) -> str:
    """Hash a password that has already passed the policy check."""
    material = password.encode("utf-8")
    if len(material) > _MAX_PASSWORD_BYTES:
        raise ValueError("password must be at most 72 bytes")
    return bcrypt.hashpw(material, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, hashed: str | None) -> bool:
    """Constant-time check. A missing hash still runs bcrypt and returns False.

    bcrypt 4 refuses a password longer than 72 bytes by raising. That raise
    would be a faster answer than a real check, so an over-long password runs
    the dummy check and fails.
    """
    material = password.encode("utf-8")
    if len(material) > _MAX_PASSWORD_BYTES or not hashed:
        bcrypt.checkpw(b"x", _dummy())
        return False
    try:
        return bcrypt.checkpw(material, hashed.encode("ascii"))
    except ValueError:
        bcrypt.checkpw(b"x", _dummy())
        return False


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, hashed: str | None) -> bool:
    return await asyncio.to_thread(verify_password, password, hashed)


def password_policy_reasons(password: str, username: str) -> list[str]:
    """Every reason the password is refused. Empty means it is acceptable."""
    reasons: list[str] = []
    if len(password) < 12:
        reasons.append("password must be at least 12 characters")
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        reasons.append("password must be at most 72 bytes")
    if password.casefold() == username.casefold():
        reasons.append("password must not equal the username")
    return reasons
