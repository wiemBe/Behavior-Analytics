"""Account administration bodies. No password is ever returned."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    username: str = Field(max_length=255)
    auth_source: Literal["LOCAL", "LDAP"]
    password: str | None = Field(default=None, max_length=1024)
    email: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    roles: list[str] = Field(default_factory=list)


class RolesIn(BaseModel):
    roles: list[str]


class PasswordResetIn(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: str = Field(max_length=1024)


class RoleOut(BaseModel):
    """One role as the administration picker lists it. No description of a person."""

    name: str
    permissions: list[str]


class UserOut(BaseModel):
    id: uuid.UUID
    username: str | None
    auth_source: str
    display_name: str | None
    email: str | None
    roles: list[str]
    disabled: bool
    disabled_at: datetime | None
    last_login_at: datetime | None
    locked_until: datetime | None
    must_change_password: bool
