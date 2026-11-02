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
    # GUIDE-3. The guide sections whose page-header link was already shown to this account.
    # Null for a principal without an account (development): nothing can be stored, so the
    # client keeps showing every link.
    guide_seen: list[str] | None = None


class PreferencesIn(BaseModel):
    """The caller's own account preferences. No other key is accepted."""

    model_config = ConfigDict(extra="forbid")

    ui_language: UiLanguage


# GUIDE-3. A guide section id is the slug the guide uses: lower-case words joined by hyphens.
GUIDE_SECTION_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
GUIDE_SECTION_MAX_LENGTH = 64
# The guide has fewer than fifty sections; the cap only keeps a misbehaving client from
# growing the account row without bound.
GUIDE_SEEN_CAP = 200


class GuideSeenIn(BaseModel):
    """One guide section the page has just shown the link for."""

    model_config = ConfigDict(extra="forbid")

    section: str = Field(
        min_length=1, max_length=GUIDE_SECTION_MAX_LENGTH, pattern=GUIDE_SECTION_PATTERN
    )
