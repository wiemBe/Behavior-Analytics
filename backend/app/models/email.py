"""Administered alert e-mail: templates and recipients (MAIL-1).

``EmailTemplate`` holds one subject/HTML/text triple. ``alert_type`` NULL is
the default template every type without its own row falls back to; a set value
must be one of the ``alert.source_type`` values. A disabled row is skipped, so
a disabled per-type template falls back to the default row, and a disabled
default row falls back to the shipped default held in code — an e-mail is never
refused because an administrator left a row switched off. Deleting the row is
the "reset to the shipped default" operation.

``EmailRecipient`` is one mailbox routing decides for: minimum severity and an
optional set of alert types. Both tables are additive (AGENTS.md rule 7) and
touch no Phase A table or enum.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

#: Every ``alert.source_type`` that may carry its own template or scope a
#: recipient, plus ``digest`` (SOC-17c): the daily digest is a mail type with its
#: own template, not an alert producer. Keeping it here lets the admin API
#: administer the digest template with the others; the recipient ``alert_types``
#: filter never selects it, because the digest is site-wide rather than
#: family-scoped. Kept beside the tables because the admin API validates against it.
EMAIL_ALERT_TYPES: tuple[str, ...] = (
    "log_source",
    "waf_endpoint",
    "ips_entity",
    "custom_rule",
    "pw_value",
    "catchall_sender",
    "qradar_instance",
    "scheduled_search",
    "digest",
)

#: The values a recipient's ``alert_types`` filter may name. ``digest`` is a
#: mail template key, not a family-scoped alert producer: a recipient filter
#: never selects it, because the digest is site-wide. Kept beside
#: ``EMAIL_ALERT_TYPES`` so the admin API can refuse it with the same 422 shape.
EMAIL_RECIPIENT_ALERT_TYPES: tuple[str, ...] = tuple(
    name for name in EMAIL_ALERT_TYPES if name != "digest"
)

_ALERT_TYPES_SQL = ", ".join(f"'{name}'" for name in EMAIL_ALERT_TYPES)


class EmailTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "email_template"
    __table_args__ = (
        CheckConstraint(
            f"alert_type IS NULL OR alert_type IN ({_ALERT_TYPES_SQL})",
            name="alert_type_known",
        ),
        CheckConstraint("length(subject) > 0", name="subject_not_empty"),
        CheckConstraint("length(html_body) > 0", name="html_body_not_empty"),
        # NULLS NOT DISTINCT: exactly one default row (alert_type NULL) can
        # exist, so "reset to the shipped default" is one DELETE, never a
        # choice between stale duplicates.
        UniqueConstraint(
            "alert_type", name="uq_email_template_alert_type", postgresql_nulls_not_distinct=True
        ),
    )

    #: NULL = the default template; otherwise an ``EMAIL_ALERT_TYPES`` value.
    alert_type: Mapped[str | None] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Sanitised on save (bleach plus the in-house CSS property allowlist).
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    #: NULL or empty = derived from the HTML body when the mail is rendered.
    text_body: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: ``created_at``/``updated_at`` come from ``TimestampMixin``; ``updated_at``
    #: is also written explicitly by the admin API so a save that changes nothing
    #: still records who looked last.
    updated_by: Mapped[str | None] = mapped_column(String(255))


class EmailRecipient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "email_recipient"
    __table_args__ = (
        CheckConstraint("length(address) > 0", name="address_not_empty"),
        CheckConstraint(
            "min_severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="min_severity_known",
        ),
        UniqueConstraint("address", name="uq_email_recipient_address"),
    )

    address: Mapped[str] = mapped_column(String(255), nullable=False)
    min_severity: Mapped[str] = mapped_column(String(16), default="LOW", nullable=False)
    #: NULL = every alert type. A JSONB array of ``EMAIL_ALERT_TYPES`` values.
    alert_types: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255))
