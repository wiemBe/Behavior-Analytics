"""Alert e-mail administration (MAIL-1): templates, recipients, preview, test send.

Every route needs ``admin:notifications``, applied at the router mount so a
route added here cannot ship unguarded; ``read:*`` does not satisfy it. The
SMTP connection and password stay in ``.env``: the settings view reports what
is configured and never the password. Every write is audited.

A template holds subject, HTML and optional text with ``{{ name }}``
placeholders from a fixed allowlist. Save refuses an unknown name (a coded 422
naming it) and stores the sanitised HTML, which the response returns so the
editor shows what will be sent. A preview renders against a fixed sample alert
of the chosen type, never a real one.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from app.alerts.routing import env_email_recipients
from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings, get_settings
from app.core.rate_limit import limiter
from app.i18n.messages import MessageRef, render
from app.i18n.refusal import CodedHTTPException
from app.models.email import (
    EMAIL_ALERT_TYPES,
    EMAIL_RECIPIENT_ALERT_TYPES,
    EmailRecipient,
    EmailTemplate,
)
from app.repositories import email as repo
from app.repositories.audit import record_audit
from app.schemas.email import (
    AlertTypeOut,
    EmailSettingsOut,
    MessageOut,
    PlaceholderOut,
    PlaceholdersOut,
    PreviewIn,
    PreviewOut,
    RecipientIn,
    RecipientListOut,
    RecipientOut,
    SendTestIn,
    SendTestOut,
    TemplateIn,
    TemplateListOut,
    TemplateOut,
    TemplateSummaryOut,
)
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services import email_delivery as delivery
from app.services import email_templates as tpl

router = APIRouter(prefix="/admin/email", tags=["admin-email"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]

#: The path segment of the default template.
DEFAULT_KEY = "default"

#: Subject prefix of a test message, so the recipient can tell it from an alert.
TEST_SUBJECT_PREFIX = "[Test] "


def _test_limit() -> str:
    """The tighter limit of the test-message action, read per request."""
    return get_settings().rate_limit_email_test


def _alert_type_for(key: str) -> str | None:
    """``default`` -> None; an alert type -> itself; anything else is a coded 422."""
    if key == DEFAULT_KEY:
        return None
    if key not in EMAIL_ALERT_TYPES:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MessageRef("email.template.alert_type_unknown", {"name": key[:64]}),
        )
    return key


def _label(alert_type: str | None) -> str:
    return "Default" if alert_type is None else tpl.ALERT_TYPE_LABELS[alert_type]


def _refuse_unknown_placeholders(*texts: str) -> None:
    unknown = tpl.unknown_placeholders(*texts)
    if unknown:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            refs=[MessageRef("email.template.unknown_placeholder", {"name": n}) for n in unknown],
        )


def _prepare(payload: TemplateIn) -> tuple[str, str, str | None]:
    """Validate and normalise a template: placeholders known, HTML sanitised,
    subject on one line. Returns subject, sanitised HTML, text."""
    subject = " ".join(payload.subject.split())
    text = payload.text_body or None
    _refuse_unknown_placeholders(subject, payload.html_body, text or "")
    html = tpl.sanitize_email_html(payload.html_body)
    if not html.strip():
        # Everything was stripped: storing an empty body would mail a blank message.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "the HTML body has no content left after unsafe markup is removed",
        )
    if not subject:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "the subject is empty")
    return subject, html, text


def _snapshot(row: EmailTemplate | None) -> dict[str, object] | None:
    """What an audit record keeps of a template: sizes, not the markup."""
    if row is None:
        return None
    return {
        "alert_type": row.alert_type,
        "subject": row.subject,
        "enabled": row.enabled,
        "html_length": len(row.html_body),
        "text_length": len(row.text_body or ""),
    }


def _recipient_snapshot(row: EmailRecipient | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "address": row.address,
        "min_severity": str(row.min_severity),
        "alert_types": row.alert_types,
        "enabled": row.enabled,
    }


def _outcome(code: str) -> MessageOut:
    # Every outcome sentence is fixed: the code needs no parameters.
    ref = MessageRef(code)
    return MessageOut(code=code, params={}, message=render(ref))


# --- settings and placeholders -------------------------------------------------


@router.get("/settings", response_model=EmailSettingsOut)
async def get_email_settings(settings: SettingsDep) -> EmailSettingsOut:
    """The relay as configured in ``.env``. Never the password."""
    return EmailSettingsOut(
        configured=settings.smtp_configured,
        host=settings.smtp_host,
        port=settings.smtp_port,
        security=settings.smtp_security_mode,
        security_source="SMTP_SECURITY" if settings.smtp_security else "SMTP_USE_TLS",
        from_address=settings.smtp_from,
        from_name=settings.smtp_from_name,
        username_set=bool(settings.smtp_username),
        ca_file_set=bool(settings.smtp_ca_file),
        site_name=settings.site_name,
        test_rate_limit=settings.rate_limit_email_test,
    )


@router.get("/placeholders", response_model=PlaceholdersOut)
async def list_placeholders() -> PlaceholdersOut:
    return PlaceholdersOut(
        syntax="{{ name }}",
        placeholders=[
            PlaceholderOut(name=p.name, description=p.description) for p in tpl.PLACEHOLDERS
        ],
    )


# --- templates -----------------------------------------------------------------


def _summaries(rows: dict[str | None, EmailTemplate]) -> list[TemplateSummaryOut]:
    out: list[TemplateSummaryOut] = []
    for alert_type in (None, *EMAIL_ALERT_TYPES):
        row = rows.get(alert_type)
        out.append(
            TemplateSummaryOut(
                key=DEFAULT_KEY if alert_type is None else alert_type,
                alert_type=alert_type,
                label=_label(alert_type),
                customised=row is not None,
                enabled=row.enabled if row is not None else False,
                updated_at=row.updated_at if row is not None else None,
                updated_by=row.updated_by if row is not None else None,
            )
        )
    return out


@router.get("/templates", response_model=TemplateListOut)
async def list_templates(session: SessionDep) -> TemplateListOut:
    rows = {row.alert_type: row for row in await repo.list_templates(session)}
    return TemplateListOut(
        templates=_summaries(rows),
        # The recipient editor's scope list. ``digest`` is a template key only:
        # a recipient filter never selects the site-wide daily digest.
        alert_types=[
            AlertTypeOut(key=k, label=tpl.ALERT_TYPE_LABELS[k])
            for k in EMAIL_RECIPIENT_ALERT_TYPES
        ],
    )


def _template_out(
    alert_type: str | None, own: EmailTemplate | None, default: EmailTemplate | None
) -> TemplateOut:
    """The row for a key, or the template it falls back to when it has none.

    A key without a row (or with a disabled one) shows what it sends today, so
    the editor can start from it; ``customised`` and ``effective_source`` say
    which it is.
    """
    own_active = own if own is not None and own.enabled else None
    default_active = default if default is not None and default.enabled else None
    if alert_type == "digest":
        # The digest has its own shipped body and never falls back to the alert
        # default: showing that template here would let an operator save an alert
        # body as the digest's content.
        digest_source: Literal["own", "shipped"]
        if own_active is not None:
            digest_subject, digest_html, digest_text = (
                own_active.subject,
                own_active.html_body,
                own_active.text_body or "",
            )
            digest_source = "own"
        else:
            shipped_digest = tpl.shipped_digest_template()
            digest_subject, digest_html, digest_text = (
                shipped_digest.subject,
                shipped_digest.html,
                shipped_digest.text,
            )
            digest_source = "shipped"
        return TemplateOut(
            key=alert_type,
            alert_type=alert_type,
            label=_label(alert_type),
            customised=own is not None,
            enabled=own.enabled if own is not None else False,
            effective_source=digest_source,
            subject=digest_subject,
            html_body=digest_html,
            text_body=digest_text,
            updated_at=own.updated_at if own is not None else None,
            updated_by=own.updated_by if own is not None else None,
        )
    source: Literal["own", "default", "shipped"]
    if own_active is not None:
        source = "own"
    elif alert_type is not None and default_active is not None:
        source = "default"
    else:
        source = "shipped"
    shown = own or default_active
    if shown is not None:
        subject, html, text = shown.subject, shown.html_body, shown.text_body or ""
    else:
        shipped = tpl.shipped_template()
        subject, html, text = shipped.subject, shipped.html, shipped.text
    return TemplateOut(
        key=DEFAULT_KEY if alert_type is None else alert_type,
        alert_type=alert_type,
        label=_label(alert_type),
        customised=own is not None,
        enabled=own.enabled if own is not None else False,
        effective_source=source,
        subject=subject,
        html_body=html,
        text_body=text,
        updated_at=own.updated_at if own is not None else None,
        updated_by=own.updated_by if own is not None else None,
    )


@router.get("/templates/{key}", response_model=TemplateOut)
async def get_template(key: str, session: SessionDep) -> TemplateOut:
    alert_type = _alert_type_for(key)
    own = await repo.get_template(session, alert_type)
    default = own if alert_type is None else await repo.get_template(session, None)
    return _template_out(alert_type, own, default)


@router.put("/templates/{key}", response_model=TemplateOut)
async def put_template(
    key: str, payload: TemplateIn, session: SessionDep, principal: PrincipalDep
) -> TemplateOut:
    """Create or replace one template. Returns the sanitised text that was stored."""
    alert_type = _alert_type_for(key)
    subject, html, text = _prepare(payload)
    row = await repo.get_template(session, alert_type)
    before = _snapshot(row)
    if row is None:
        row = EmailTemplate(alert_type=alert_type)
        session.add(row)
    row.subject = subject
    row.html_body = html
    row.text_body = text
    row.enabled = payload.enabled
    row.updated_by = principal.subject
    await session.flush()
    await session.refresh(row)
    await record_audit(
        session,
        action="email_template.put",
        actor=principal.subject,
        object_type="email_template",
        object_id=key,
        detail={"before": before, "after": _snapshot(row)},
    )
    default = row if alert_type is None else await repo.get_template(session, None)
    return _template_out(alert_type, row, default)


@router.delete("/templates/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(key: str, session: SessionDep, principal: PrincipalDep) -> Response:
    """Reset to the shipped default (the default key) or to the default template."""
    alert_type = _alert_type_for(key)
    row = await repo.get_template(session, alert_type)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no stored template for this key")
    before = _snapshot(row)
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        action="email_template.delete",
        actor=principal.subject,
        object_type="email_template",
        object_id=key,
        detail={"before": before, "after": None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- preview and test send -----------------------------------------------------


def _render_sample(
    payload: PreviewIn, settings: Settings
) -> tuple[tpl.RenderedEmail, str]:
    """Validate, sanitise and render unsaved text against the fixed sample alert."""
    alert_type = payload.alert_type
    if alert_type is not None and alert_type not in EMAIL_ALERT_TYPES:
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MessageRef("email.template.alert_type_unknown", {"name": alert_type[:64]}),
        )
    subject, html, text = _prepare(payload)
    template = tpl.ResolvedTemplate(subject=subject, html=html, text=text or "")
    rendered = tpl.render_template(template, tpl.sample_values(alert_type, settings))
    return rendered, html


@router.post("/preview", response_model=PreviewOut)
async def preview(payload: PreviewIn, settings: SettingsDep) -> PreviewOut:
    """Render unsaved text against a fixed sample alert of the chosen type."""
    rendered, sanitized = _render_sample(payload, settings)
    return PreviewOut(
        subject=rendered.subject,
        html=rendered.html,
        text=rendered.text,
        sanitized_html_body=sanitized,
        sample_alert_type=payload.alert_type or DEFAULT_KEY,
    )


@router.post("/test", response_model=SendTestOut)
@limiter.limit(_test_limit)
async def send_test(
    request: Request,
    response: Response,
    payload: SendTestIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> SendTestOut:
    """Send the rendered sample to one mailbox through the configured relay.

    Rate limited (``RATE_LIMIT_EMAIL_TEST``). The outcome is a coded sentence;
    the relay's own reply text is never returned.
    """
    address = payload.to.strip()
    if not delivery.valid_mailbox(address):
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MessageRef("email.recipient.address_invalid", {"address": address[:80]}),
        )
    rendered, _ = _render_sample(payload, settings)
    message = delivery.build_message(
        settings,
        address,
        delivery.MailParts(
            subject=TEST_SUBJECT_PREFIX + rendered.subject, html=rendered.html, text=rendered.text
        ),
    )
    code = "sent"
    try:
        await asyncio.to_thread(delivery.deliver, settings, message)
    except delivery.EmailDeliveryError as exc:
        code = exc.code
    ok = code == "sent"
    await record_audit(
        session,
        action="email.test_send",
        actor=principal.subject,
        object_type="email_test",
        object_id=address,
        outcome="SUCCESS" if ok else "FAILURE",
        detail={"result": code, "alert_type": payload.alert_type},
    )
    return SendTestOut(ok=ok, outcome=_outcome(f"email.test.{code}"))


# --- recipients ----------------------------------------------------------------


def _checked_recipient(payload: RecipientIn) -> tuple[str, list[str] | None]:
    address = payload.address.strip().lower()
    if not delivery.valid_mailbox(address):
        raise CodedHTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            MessageRef("email.recipient.address_invalid", {"address": address[:80]}),
        )
    types: list[str] | None = None
    if payload.alert_types is not None:
        unknown = [
            t for t in payload.alert_types if t not in EMAIL_RECIPIENT_ALERT_TYPES
        ]
        if unknown:
            raise CodedHTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                refs=[
                    MessageRef("email.recipient.alert_type_unknown", {"name": n[:64]})
                    for n in unknown
                ],
            )
        types = sorted(set(payload.alert_types))
    return address, types


def _duplicate(address: str) -> CodedHTTPException:
    return CodedHTTPException(
        status.HTTP_409_CONFLICT, MessageRef("email.recipient.duplicate", {"address": address})
    )


@router.get("/recipients", response_model=RecipientListOut)
async def list_recipients(session: SessionDep) -> RecipientListOut:
    rows = await repo.list_recipients(session)
    return RecipientListOut(
        recipients=[RecipientOut.model_validate(row) for row in rows],
        from_env=env_email_recipients(),
    )


@router.post("/recipients", response_model=RecipientOut, status_code=status.HTTP_201_CREATED)
async def create_recipient(
    payload: RecipientIn, session: SessionDep, principal: PrincipalDep
) -> RecipientOut:
    address, types = _checked_recipient(payload)
    if await repo.get_recipient_by_address(session, address) is not None:
        raise _duplicate(address)
    row = EmailRecipient(
        address=address,
        min_severity=payload.min_severity.value,
        alert_types=types,
        enabled=payload.enabled,
        updated_by=principal.subject,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:  # a concurrent create of the same address
        raise _duplicate(address) from exc
    await session.refresh(row)
    await record_audit(
        session,
        action="email_recipient.create",
        actor=principal.subject,
        object_type="email_recipient",
        object_id=str(row.id),
        detail={"before": None, "after": _recipient_snapshot(row)},
    )
    return RecipientOut.model_validate(row)


@router.put("/recipients/{recipient_id}", response_model=RecipientOut)
async def update_recipient(
    recipient_id: uuid.UUID,
    payload: RecipientIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> RecipientOut:
    row = await repo.get_recipient(session, recipient_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recipient not found")
    address, types = _checked_recipient(payload)
    other = await repo.get_recipient_by_address(session, address)
    if other is not None and other.id != row.id:
        raise _duplicate(address)
    before = _recipient_snapshot(row)
    row.address = address
    row.min_severity = payload.min_severity.value
    row.alert_types = types
    row.enabled = payload.enabled
    row.updated_by = principal.subject
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate(address) from exc
    await session.refresh(row)
    await record_audit(
        session,
        action="email_recipient.update",
        actor=principal.subject,
        object_type="email_recipient",
        object_id=str(row.id),
        detail={"before": before, "after": _recipient_snapshot(row)},
    )
    return RecipientOut.model_validate(row)


@router.delete("/recipients/{recipient_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipient(
    recipient_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> Response:
    row = await repo.get_recipient(session, recipient_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recipient not found")
    before = _recipient_snapshot(row)
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        action="email_recipient.delete",
        actor=principal.subject,
        object_type="email_recipient",
        object_id=str(recipient_id),
        detail={"before": before, "after": None},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
