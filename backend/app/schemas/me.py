"""API contract for the caller's identity and permissions."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MeOut(BaseModel):
    """Resolved caller with their granted and effective permissions.

    The session fields default so a development principal, which has no
    account, still satisfies the contract.
    """

    subject: str
    email: str | None
    permissions: list[str]
    effective_permissions: list[str]
    username: str | None = None
    auth_source: str | None = None
    must_change_password: bool = False
    roles: list[str] = Field(default_factory=list)