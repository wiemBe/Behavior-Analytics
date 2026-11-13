"""The pinned, guarded POST to a named webhook destination (SOC-12b/12d).

One implementation for the admin "send test" and the dispatcher's delivery: the URL guard
runs at send time, the request goes to the checked address (SNI and `Host` keep the
configured name), a transport is called directly so there is no redirect or proxy layer and
no HTTPX request log, and the response is read at most 64 KiB and discarded. The resolver
and the transport factory are parameters so tests never touch the network.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from typing import Literal, cast

import httpx

from app.alerts.notifiers.url_guard import GuardedURL, URLGuardError, guard_url, resolve_host
from app.alerts.notifiers.webhook_v1 import SIGNATURE_HEADER, sign_v1
from app.core.config import get_settings
from app.models.notification_destination import NotificationDestination

#: A `WEBHOOK` row whose target starts with this names a stored destination, never a URL.
DESTINATION_TARGET_PREFIX = "destination:"


def destination_target_id(target: str) -> uuid.UUID | None:
    """The destination id a `destination:<uuid>` target names, or None."""
    if not target.startswith(DESTINATION_TARGET_PREFIX):
        return None
    try:
        return uuid.UUID(target[len(DESTINATION_TARGET_PREFIX) :])
    except ValueError:
        return None


MAX_RESPONSE_BYTES = 64 * 1024
READ_CHUNK_BYTES = 8 * 1024
MAX_CUSTOM_HEADERS = 5
MAX_HEADER_VALUE_LENGTH = 4096

#: Names the sender owns, plus the hop-by-hop names that change framing.
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
FORBIDDEN_HEADER_NAMES = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "idempotency-key",
        SIGNATURE_HEADER.lower(),
        "transfer-encoding",
        "connection",
        "te",
        "upgrade",
        "expect",
    }
)

SendClass = Literal[
    "ok", "timeout", "transport", "http_status", "redirect_refused", "ssrf_refused"
]

Resolver = Callable[[str, int], tuple[str, ...]]
TransportFactory = Callable[[], httpx.AsyncBaseTransport]


def http_transport() -> httpx.AsyncBaseTransport:
    # A transport has no redirect or environment-proxy layer. Calling it directly
    # also avoids HTTPX's INFO request log, which includes the sensitive path/query.
    return httpx.AsyncHTTPTransport(retries=0)


async def guard(
    url: str, *, allow_http: bool, allow_private: bool, resolver: Resolver = resolve_host
) -> GuardedURL:
    return await asyncio.to_thread(
        guard_url, url, allow_http=allow_http, allow_private=allow_private, resolver=resolver
    )


def encode_headers(headers: dict[str, str]) -> str:
    return json.dumps(headers, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def decode_headers(row: NotificationDestination) -> dict[str, str]:
    value = json.loads(row.headers)
    if not isinstance(value, dict) or not all(
        isinstance(name, str) and isinstance(header, str) for name, header in value.items()
    ):
        raise RuntimeError("stored notification destination headers are malformed")
    return cast(dict[str, str], value)


class StoredHeadersInvalid(Exception):
    """Stored custom headers cannot be sent, and a retry will not repair them."""

    def __init__(self) -> None:
        super().__init__("payload_invalid")


def checked_custom_headers(value: dict[str, str]) -> dict[str, str]:
    """The admin schema and the send path share this check.

    The message names the header and never the value: a 422 and a log line must
    not echo a secret the operator typed into a header.
    """
    if len(value) > MAX_CUSTOM_HEADERS:
        raise ValueError("at most five custom headers are allowed")
    for name, header_value in value.items():
        if not HEADER_NAME.fullmatch(name) or name.lower() in FORBIDDEN_HEADER_NAMES:
            raise ValueError(f"custom header name is not allowed: {name[:64]}")
        if len(header_value) > MAX_HEADER_VALUE_LENGTH or any(
            char in header_value for char in ("\r", "\n", "\x00")
        ):
            raise ValueError(f"custom header value is not allowed for: {name[:64]}")
    return value


def headers_for_send(row: NotificationDestination) -> dict[str, str]:
    """Decode a stored header block and refuse one the schema would not have saved."""
    try:
        decoded = decode_headers(row)
    except (RuntimeError, ValueError, TypeError, UnicodeError) as exc:
        raise StoredHeadersInvalid() from exc
    try:
        return checked_custom_headers(decoded)
    except ValueError as exc:
        raise StoredHeadersInvalid() from exc


def transport_failure_class(exc: BaseException) -> SendClass | None:
    """``timeout`` or ``transport`` for a socket or protocol failure, else None.

    ``h11.LocalProtocolError`` is not an ``httpx.TransportError``. Callers still
    keep only the class: the exception text can carry the URL.
    """
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (httpx.HTTPError, OSError)):
        return "transport"
    if type(exc).__module__.split(".", 1)[0] in {"h11", "httpcore"}:
        return "transport"
    return None


async def _discard_body(response: httpx.Response) -> None:
    if response.is_stream_consumed:
        return
    read = 0
    async for chunk in response.aiter_raw(chunk_size=READ_CHUNK_BYTES):
        read += len(chunk)
        if read >= MAX_RESPONSE_BYTES:
            break


def _guard_class(exc: URLGuardError) -> SendClass:
    if exc.code in {"notifications.private_refused", "notifications.http_refused"}:
        return "ssrf_refused"
    return "transport"


async def send_to_destination(
    row: NotificationDestination,
    body: bytes,
    headers: dict[str, str],
    *,
    resolver: Resolver = resolve_host,
    transport_factory: TransportFactory = http_transport,
    deadline_seconds: float | None = None,
) -> tuple[SendClass, int | None]:
    """POST ``body`` to the destination; return the failure class and HTTP status.

    ``headers`` carries the content type and idempotency key. The signature (when the
    destination has a secret), the stored custom headers and `Host` are added here.
    ``deadline_seconds`` is one wall-clock budget for the whole send. The 10 second
    values on the request are idle timeouts per socket operation, not that budget.
    """
    # A stored block that fails the schema is permanent. Check it before any connect.
    custom = headers_for_send(row)
    budget = (
        get_settings().notify_send_deadline_seconds
        if deadline_seconds is None
        else deadline_seconds
    )
    try:
        async with asyncio.timeout(budget):
            try:
                checked = await guard(
                    row.url,
                    allow_http=row.allow_http,
                    allow_private=row.allow_private,
                    resolver=resolver,
                )
            except URLGuardError as exc:
                return _guard_class(exc), None
            sent = dict(headers)
            sent["Host"] = checked.host_header
            if row.hmac_secret is not None:
                sent[SIGNATURE_HEADER] = sign_v1(body, row.hmac_secret)
            sent.update(custom)
            # `content=` is what makes httpx set Content-Length and write these
            # exact bytes. A stream with no framing header is sent as length 0
            # and then rejected by h11 once the body follows the headers.
            request = httpx.Request(
                "POST",
                checked.pinned_url(checked.addresses[0]),
                content=body,
                headers=sent,
                extensions={
                    "sni_hostname": checked.hostname,
                    "timeout": httpx.Timeout(10.0).as_dict(),
                },
            )
            async with transport_factory() as transport:
                response = await transport.handle_async_request(request)
                response.request = request
                response_status = response.status_code
                try:
                    await _discard_body(response)
                finally:
                    await response.aclose()
    except Exception as exc:
        failure = transport_failure_class(exc)
        if failure is None:
            raise
        return failure, None
    if 200 <= response_status < 300:
        return "ok", response_status
    if 300 <= response_status < 400:
        return "redirect_refused", response_status
    return "http_status", response_status
