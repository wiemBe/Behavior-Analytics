"""API contract for the caller's identity, permissions, and own preferences."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# D-37. The interface languages the product ships. The column's check
# constraint allows the same two values.
UiLanguage = Literal["en", "tr"]


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
    ui_language: UiLanguage = "en"


class PreferencesIn(BaseModel):
    """The caller's own account preferences. No other key is accepted."""

    model_config = ConfigDict(extra="forbid")

    ui_language: UiLanguage
