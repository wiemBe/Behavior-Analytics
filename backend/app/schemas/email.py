"""API schemas for alert e-mail administration (MAIL-1).

Every field is named for the admin page built on it (MAIL-2). Nothing here
carries the SMTP password; the settings view reports only whether a username
or a CA file is set.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Severity

#: Upper bounds. A template is a page of HTML, not a document store.
SUBJECT_MAX = 512
HTML_MAX = 100_000
TEXT_MAX = 50_000

SecurityMode = Literal["starttls", "ssl", "none"]


class EmailSettingsOut(BaseModel):
    """The transport as configured in ``.env``. Read-only; never the password."""

    #: True when ``SMTP_HOST`` is set. False means no mail can leave this site.
    configured: bool
    host: str
    port: int
    #: The resolved mode: ``SMTP_SECURITY``, else derived from ``SMTP_USE_TLS``.
    security: SecurityMode
    #: Which setting decided ``security``: ``SMTP_SECURITY`` or ``SMTP_USE_TLS``.
    security_source: Literal["SMTP_SECURITY", "SMTP_USE_TLS"]
    from_address: str
    from_name: str
    username_set: bool
    ca_file_set: bool
    site_name: str
    #: The tighter limit on the test-message action, for example ``5/minute``.
    test_rate_limit: str


class PlaceholderOut(BaseModel):
    name: str
    description: str


class PlaceholdersOut(BaseModel):
    #: The syntax, for the editor's help text: ``{{ name }}``.
    syntax: str
    placeholders: list[PlaceholderOut]


class AlertTypeOut(BaseModel):
    #: The value templates and recipients use (``alert.source_type``).
    key: str
    label: str


class TemplateSummaryOut(BaseModel):
    """One row of the template list: the default plus one entry per alert type."""

    #: ``default`` or an alert type key; the path segment of the template routes.
    key: str
    alert_type: str | None
    label: str
    #: True when a stored row exists (enabled or not).
    customised: bool
    enabled: bool
    updated_at: datetime | None
    updated_by: str | None


class TemplateListOut(BaseModel):
    templates: list[TemplateSummaryOut]
    alert_types: list[AlertTypeOut]


class TemplateOut(BaseModel):
    key: str
    alert_type: str | None
    label: str
    #: True when a stored row exists for this key.
    customised: bool
    #: Meaningful only when ``customised``; a type without a row reports true.
    enabled: bool
    #: What this key sends today: its own row, else the default row, else the shipped
    #: default. ``own`` | ``default`` | ``shipped``.
    effective_source: Literal["own", "default", "shipped"]
    subject: str
    html_body: str
    #: Empty when the plain-text part is derived from the HTML.
    text_body: str
    updated_at: datetime | None
    updated_by: str | None


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=SUBJECT_MAX)
    html_body: str = Field(min_length=1, max_length=HTML_MAX)
    #: Null or empty: the plain-text part is derived from the HTML body.
    text_body: str | None = Field(default=None, max_length=TEXT_MAX)
    enabled: bool = True


class PreviewIn(TemplateIn):
    """A preview renders unsaved text, so it carries the same fields plus the type
    whose fixed sample alert fills the placeholders. Null is the generic sample."""

    alert_type: str | None = None


class PreviewOut(BaseModel):
    subject: str
    #: The rendered mail body, sanitised, with the sample values escaped.
    html: str
    text: str
    #: The template HTML after sanitising, so the editor shows what will be stored.
    sanitized_html_body: str
    #: The sample alert's values, so the page can say what they are. Fixed text.
    sample_alert_type: str


class SendTestIn(PreviewIn):
    #: One mailbox typed by the administrator.
    to: str = Field(min_length=3, max_length=254)


class MessageOut(BaseModel):
    """A coded sentence, as the other admin routes return one."""

    code: str
    params: dict[str, object]
    message: str


class SendTestOut(BaseModel):
    ok: bool
    #: ``email.test.sent`` or ``email.test.<failure>``; the code, parameters and the
    #: English sentence of the outcome. Never the relay's own reply text.
    outcome: MessageOut


class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=3, max_length=254)
    min_severity: Severity = Severity.LOW
    #: Null: every alert type. A list limits the recipient to those types.
    alert_types: list[str] | None = Field(default=None, min_length=1, max_length=8)
    enabled: bool = True


class RecipientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    address: str
    min_severity: Severity
    alert_types: list[str] | None
    enabled: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str | None


class RecipientListOut(BaseModel):
    recipients: list[RecipientOut]
    #: Addresses from ``NOTIFY_EMAIL_RECIPIENTS`` in ``.env``. Read-only here: they
    #: receive every alert at LOW and above, whatever the table says.
    from_env: list[str]
