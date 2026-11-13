"""Redacted admin API schemas for named webhook destinations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.alerts.notifiers.destination_sender import checked_custom_headers


class DestinationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    url: str = Field(min_length=1, max_length=2048)
    allow_http: bool = False
    allow_private: bool = False
    hmac_secret: str | None = Field(default=None, min_length=1, max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", "url")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("headers")
    @classmethod
    def _headers(cls, value: dict[str, str]) -> dict[str, str]:
        return checked_custom_headers(value)


class DestinationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    allow_http: bool | None = None
    allow_private: bool | None = None
    hmac_secret: str | None = Field(default=None, min_length=1, max_length=4096)
    headers: dict[str, str] | None = None

    @field_validator("name", "url")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("headers")
    @classmethod
    def _optional_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return None if value is None else checked_custom_headers(value)

    @model_validator(mode="after")
    def _only_secret_may_be_cleared(self) -> Self:
        for name in self.model_fields_set - {"hmac_secret"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} may not be null")
        return self


class DestinationOut(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    url_display: str
    allow_http: bool
    allow_private: bool
    secret_set: bool
    header_names: list[str]
    last_test_at: datetime | None
    last_test_status: int | None
    last_test_class: str | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class MessageOut(BaseModel):
    code: str
    params: dict[str, object]
    message: str


TestClass = Literal[
    "ok",
    "transport",
    "timeout",
    "http_status",
    "ssrf_refused",
    "redirect_refused",
    "payload_invalid",
]


class DestinationTestOut(BaseModel):
    ok: bool
    status: int | None
    result: TestClass
    outcome: MessageOut
