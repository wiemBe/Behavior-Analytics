"""Request and response bodies for sign-in and directory configuration.

Password fields hide themselves in validation errors. A 422 must not echo
the password the caller typed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Hidden(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)


class LoginIn(_Hidden):
    username: str = Field(max_length=255)
    # Empty is refused by the handler. A minimum here would be a different
    # status and a different sentence from a wrong password.
    password: str = Field(max_length=1024)


class LoginOut(BaseModel):
    username: str
    auth_source: str
    must_change_password: bool
    roles: list[str]


class PasswordChangeIn(_Hidden):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class LdapConfigIn(BaseModel):
    enabled: bool
    urls: list[str]
    start_tls: bool = False
    ca_pem: str | None = None
    bind_template: str
    timeout_seconds: int
    user_search_base: str | None = None
    version: int


class LdapConfigOut(BaseModel):
    enabled: bool
    urls: list[str]
    start_tls: bool
    ca_pem: str | None
    bind_template: str
    timeout_seconds: int
    user_search_base: str | None
    version: int
    updated_by: str | None
    updated_at: datetime | None


class LdapTestIn(_Hidden):
    enabled: bool = False
    urls: list[str]
    start_tls: bool = False
    ca_pem: str | None = None
    bind_template: str
    timeout_seconds: int = 5
    user_search_base: str | None = None
    username: str = Field(max_length=255)
    password: str = Field(max_length=1024)


class TlsResult(BaseModel):
    status: str
    reason: str | None
    trust: str


class CertificateResult(BaseModel):
    subject: str
    issuer: str
    not_after: str


class BindResult(BaseModel):
    status: str
    reason: str | None


class LdapTestOut(BaseModel):
    tls: TlsResult
    certificate: CertificateResult | None
    bind: BindResult
    latency_ms: int
