"""Seed data for development.

Creates the default RBAC roles, the default QRadar instance, and runs one
inventory sync via the configured provider (mock by default). Idempotent —
safe to run repeatedly.

The passwordless seed administrator is not created. A row with no password
would be an administrator that nobody can sign in as, and a production
migration must not leave one behind. The first administrator is created with
`python -m app.cli.users create-admin`, which prints a one-time password.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_sessionmaker
from app.models.identity import Role
from app.providers.factory import build_provider
from app.services.inventory_sync import InventorySyncService

_ROLES = {
    "admin": [
        "admin:*", "search:execute", "search:write", "alert:ack", "alert:resolve",
        "log_source:write", "rule:write", "coverage:write",
        "admin:classification",
    ],
    # Every read route is guarded, so an analyst needs `read:*` to see anything.
    # The write permissions cover the SOC-owned metadata the analyst curates:
    # rule ownership/MITRE mapping and the technique mappings coverage rests on.
    # An analyst also saves their own dashboards; sharing one needs `admin:dashboards`.
    "analyst": [
        "read:*", "search:execute", "alert:ack", "alert:resolve", "log_source:write",
        "rule:write", "coverage:write", "dashboard:write",
    ],
    "viewer": ["read:*"],
}
# Detection engineering (D-17): everything an analyst holds, plus the writes a rule
# author needs. No `admin:*`. `admin:*` already satisfies `custom_rule:write` and
# `property_watch:write` (`Principal.has` grants every permission to that wildcard), so
# the strings are not added to the admin role above.
_ROLES["detection_engineer"] = [
    *_ROLES["analyst"],
    "search:write",
    "custom_rule:write",
    "property_watch:write",
]


async def ensure_default_roles(session: AsyncSession) -> None:
    """Create the default roles, or add any permission they are missing.

    Additive only: a permission an operator granted is never removed. The
    break-glass command depends on the admin role containing ``admin:*``.
    """
    for name, perms in _ROLES.items():
        existing = await session.scalar(select(Role).where(Role.name == name))
        if existing is None:
            session.add(Role(name=name, permissions=perms))
        else:
            missing = [p for p in perms if p not in existing.permissions]
            if missing:
                existing.permissions = [*existing.permissions, *missing]
    await session.flush()


async def seed() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        await ensure_default_roles(session)

        provider = build_provider()
        try:
            svc = InventorySyncService(session, provider)
            instance = await svc.ensure_default_instance()
            result = await svc.sync(instance)
        finally:
            await provider.aclose()

        await session.commit()
        print(  # CLI feedback
            f"seed complete: {result.created} created, {result.updated} updated "
            f"via {result.provider}"
        )


if __name__ == "__main__":
    asyncio.run(seed())
