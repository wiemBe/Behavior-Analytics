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
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.models.identity import Role
from app.models.instance import QRadarInstance
from app.providers.factory import build_provider
from app.services.feature_switches import effective_settings, resolve, unreadable_features
from app.services.inventory_sync import InventorySyncService

_ROLES = {
    "admin": [
        "admin:*", "search:execute", "search:write", "alert:ack", "alert:resolve",
        "log_source:write", "rule:write", "coverage:write",
        "admin:classification", "anomaly:triage", "alert:suppress",
    ],
    # Every read route is guarded, so an analyst needs `read:*` to see anything.
    # The write permissions cover the SOC-owned metadata the analyst curates:
    # rule ownership/MITRE mapping and the technique mappings coverage rests on.
    # An analyst also saves their own dashboards; sharing one needs `admin:dashboards`.
    "analyst": [
        "read:*", "search:execute", "alert:ack", "alert:resolve", "log_source:write",
        "rule:write", "coverage:write", "dashboard:write", "anomaly:triage",
        "alert:suppress",
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


async def _ensure_default_instance(session: AsyncSession) -> QRadarInstance:
    """Return the first stored instance, creating the bootstrap default when empty.

    Mirrors ``InventorySyncService.ensure_default_instance`` so the seed creates
    the same row on both paths. Keeping it here lets the D-99 skip path commit
    that row without building a provider at all.
    ``test_default_instance_helpers_create_identical_rows`` pins this parity.
    """
    instance = await session.scalar(select(QRadarInstance).limit(1))
    if instance is not None:
        return instance
    instance = QRadarInstance(
        id=uuid.uuid4(),
        name="default",
        console_host="mock",
        provider_kind="mock",
    )
    session.add(instance)
    await session.flush()
    return instance


async def seed() -> None:
    base = get_settings()
    # D-99: read the switches in their own short session. A dropped or otherwise
    # unreadable table makes `resolve` catch a DB error that leaves *that* transaction
    # aborted; isolating the read keeps the seed's single commit on the main session
    # intact, so the default roles and instance still commit when the sync is skipped.
    try:
        async with get_sessionmaker()() as switch_session:
            resolved = await resolve(switch_session, base)
    except Exception:
        resolved = unreadable_features()
    settings = effective_settings(base, resolved)
    reason = resolved.reason

    maker = get_sessionmaker()
    async with maker() as session:
        await ensure_default_roles(session)
        instance = await _ensure_default_instance(session)

        if reason is None:
            # One snapshot for the provider's strict mode and the sync's policy (P1 m3).
            provider = build_provider(settings)
            try:
                svc = InventorySyncService(session, provider, settings=settings)
                result = await svc.sync(instance)
            finally:
                await provider.aclose()
            message = (
                f"seed complete: {result.created} created, {result.updated} updated "
                f"via {result.provider}"
            )
        else:
            # D-99 (fail closed): no provider, no walk, no source row touched. The
            # operator re-runs the seed or the normal sync once the table reads.
            message = (
                f"seed: inventory sync skipped (reason: {reason}); "
                "default roles and instance committed"
            )

        await session.commit()
        print(message)  # CLI feedback


if __name__ == "__main__":
    asyncio.run(seed())
