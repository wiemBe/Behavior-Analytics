"""Caller identity and permissions endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_principal
from app.schemas.me import MeOut
from app.security import rbac
from app.security.rbac import Principal

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


@router.get("", response_model=MeOut)
async def get_me(principal: Principal = Depends(get_principal)) -> MeOut:
    """Return the authenticated caller's identity and permissions.

    No additional permission is required: the endpoint returns only the
    caller's own subject, email and granted permissions, plus the derived
    effective permissions. Nothing secret is exposed.
    """
    granted = sorted(principal.permissions)
    effective = _effective_permissions(principal)
    return MeOut(
        subject=principal.subject,
        email=principal.email,
        permissions=granted,
        effective_permissions=effective,
    )