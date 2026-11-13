"""Concrete notifiers: Teams, Slack, generic webhook, email, syslog.

Each maps a NotificationMessage onto the channel's wire format. HTTP channels
share an injected httpx.AsyncClient so tests can mock transport with respx and
production reuses connections. No secret is logged; the webhook URL is the
credential and is passed as the target, never emitted. The response is read
raw, at most 64 KiB, and a redirect is a permanent failure: the client does
not follow it.
"""

from __future__ import annotations

import json
import logging
import socket

import httpx

from app.alerts.notifiers.base import NotificationMessage, Notifier, NotifierError
from app.core.config import Settings, get_settings
from app.models.enums import NotificationChannel, Severity

logger = logging.getLogger("app.notifiers")

_SEVERITY_COLOR = {
    Severity.CRITICAL: "D70000",
    Severity.HIGH: "E8710A",
    Severity.MEDIUM: "E3B341",
    Severity.LOW: "2EA043",
    Severity.INFO: "0969DA",
}

# Syslog severity per RFC 5424 (we map onto user.* facility).
_SYSLOG_SEVERITY = {
    Severity.CRITICAL: 2, Severity.HIGH: 3, Severity.MEDIUM: 4,
    Severity.LOW: 5, Severity.INFO: 6,
}

# Same bound as the named-destination sender: raw bytes, no decompression.
_MAX_RESPONSE_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 8 * 1024


async def _discard_body(response: httpx.Response) -> None:
    """Read at most 64 KiB of the raw body and drop it.

    ``aiter_raw`` does not honour ``Content-Encoding``, so a gzip body stays
    compressed and cannot expand a small payload into a large allocation.
    """
    if response.is_stream_consumed:
        return
    read = 0
    async for chunk in response.aiter_raw(chunk_size=_READ_CHUNK_BYTES):
        read += len(chunk)
        if read >= _MAX_RESPONSE_BYTES:
            break


class _HttpNotifier(Notifier):
    def __init__(self, client: httpx.AsyncClient, *, timeout: float = 10.0) -> None:
        self._client = client
        self._timeout = timeout

    async def _post(self, url: str, payload: dict) -> None:
        try:
            # stream=True skips the client's decoded ``aread``. follow_redirects
            # is pinned off even when the injected client would follow.
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                timeout=self._timeout,
                follow_redirects=False,
            ) as resp:
                await _discard_body(resp)
                status = resp.status_code
        except httpx.HTTPError as exc:
            # HTTPX exception text may contain the request URL, whose path or
            # query is the webhook credential. Persist only the error class.
            raise NotifierError(f"transport error: {type(exc).__name__}") from exc
        if 200 <= status < 300:
            return
        if status >= 500 or status == 429:
            raise NotifierError(f"retryable status {status}")
        # 3xx is not a delivery: the Location is not fetched. 4xx will not
        # succeed on retry. Both are permanent, as on the named-destination path.
        raise NotifierError(f"permanent status {status}", permanent=True)


class TeamsNotifier(_HttpNotifier):
    channel = NotificationChannel.TEAMS

    async def send(self, target: str, message: NotificationMessage) -> None:
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": _SEVERITY_COLOR.get(message.severity, "0969DA"),
            "summary": message.summary_line(),
            "title": message.summary_line(),
            "text": message.body,
            "sections": [
                {
                    "facts": [
                        {"name": k, "value": str(v)} for k, v in message.fields.items()
                    ]
                }
            ],
        }
        await self._post(target, card)


class SlackNotifier(_HttpNotifier):
    channel = NotificationChannel.SLACK

    async def send(self, target: str, message: NotificationMessage) -> None:
        blocks: list[dict[str, object]] = [
            {"type": "header",
             "text": {"type": "plain_text", "text": message.summary_line()[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": message.body[:2900]}},
        ]
        if message.fields:
            fields = [
                {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
                for k, v in list(message.fields.items())[:10]
            ]
            blocks.append({"type": "section", "fields": fields})
        await self._post(target, {"text": message.summary_line(), "blocks": blocks})


class GenericWebhookNotifier(_HttpNotifier):
    channel = NotificationChannel.WEBHOOK

    async def send(self, target: str, message: NotificationMessage) -> None:
        await self._post(
            target,
            {
                "title": message.title,
                "body": message.body,
                "severity": message.severity.value,
                "transition": message.transition.value,
                "alert_id": message.alert_id,
                "fingerprint": message.fingerprint,
                "fields": message.fields,
            },
        )


class EmailNotifier(Notifier):
    """SMTP e-mail: a multipart text + HTML message rendered from the template
    the dispatcher chose for the alert's type (the shipped default when none).

    Runs the blocking smtplib call in a thread so it does not block the event
    loop. The relay, its security mode and the credentials come from
    ``Settings`` (MAIL-1); the transport lives in ``app/services/email_delivery``.
    """

    channel = NotificationChannel.EMAIL

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings

    async def send(self, target: str, message: NotificationMessage) -> None:
        import asyncio

        await asyncio.to_thread(self._send_sync, target, message)

    def _send_sync(self, target: str, message: NotificationMessage) -> None:
        from app.services.email_delivery import (
            EmailDeliveryError,
            MailParts,
            build_message,
            deliver,
            valid_mailbox,
        )
        from app.services.email_templates import render_for_message, shipped_template

        settings = self._settings or get_settings()
        if not settings.smtp_configured:
            raise NotifierError("SMTP host not configured", permanent=True)
        if not valid_mailbox(target):
            # A stored or env recipient with a CR/LF or a display name would
            # reach a header; refuse it here as well as at the admin API.
            raise NotifierError("recipient address is not a plain mailbox", permanent=True)
        rendered = render_for_message(message.template or shipped_template(), message, settings)
        email = build_message(
            settings,
            target,
            MailParts(subject=rendered.subject, html=rendered.html, text=rendered.text),
        )
        try:
            deliver(settings, email)
        except EmailDeliveryError as exc:
            # The code only: a relay's reply text never reaches the audit row.
            raise NotifierError(str(exc), permanent=exc.permanent) from exc


class SyslogNotifier(Notifier):
    """RFC 5424-ish syslog over UDP. `target` is 'host:port'."""

    channel = NotificationChannel.SYSLOG
    _FACILITY = 1  # user-level

    async def send(self, target: str, message: NotificationMessage) -> None:
        import asyncio

        await asyncio.to_thread(self._send_sync, target, message)

    def _send_sync(self, target: str, message: NotificationMessage) -> None:
        host, _, port_s = target.partition(":")
        port = int(port_s or 514)
        sev = _SYSLOG_SEVERITY.get(message.severity, 6)
        pri = self._FACILITY * 8 + sev
        payload = json.dumps(
            {
                "title": message.title,
                "transition": message.transition.value,
                "fingerprint": message.fingerprint,
                "fields": message.fields,
            }
        )
        line = f"<{pri}>qradar-observability: {message.summary_line()} {payload}"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(line.encode()[:2048], (host, port))
            finally:
                sock.close()
        except OSError as exc:
            raise NotifierError(f"syslog transport error: {exc}") from exc
