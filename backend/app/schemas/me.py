"""API contract for the caller's identity and permissions."""

from __future__ import annotations

from pydantic import BaseModel


class MeOut(BaseModel):
    """Resolved caller with their granted and effective permissions."""

    subject: str
    email: str | None
    permissions: list[str]
    effective_permissions: list[str]