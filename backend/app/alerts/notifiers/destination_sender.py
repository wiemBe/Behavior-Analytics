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
import uuid
from collections.abc import Callable
from typing import Literal, cast

import httpx

from app.alerts.notifiers.url_guard import GuardedURL, URLGuardError, guard_url, resolve_host
from app.alerts.notifiers.webhook_v1 import SIGNATURE_HEADER, sign_v1
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


async def _discard_body(response: httpx.Response) -> None:
    if response.is_stream_consumed:
        return
    read = 0
    async for chunk in response.aiter_raw(chunk_size=READ_CHUNK_BYTES):
        read += len(chunk)
        if read >= MAX_RESPONSE_BYTES:
            break


class _BodyStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self.body


async def send_to_destination(
    row: NotificationDestination,
    body: bytes,
    headers: dict[str, str],
    *,
    resolver: Resolver = resolve_host,
    transport_factory: TransportFactory = http_transport,
) -> tuple[SendClass, int | None]:
    """POST ``body`` to the destination; return the failure class and HTTP status.

    ``headers`` carries the content type and idempotency key. The signature (when the
    destination has a secret), the stored custom headers and `Host` are added here.
    """
    try:
        checked = await guard(
            row.url,
            allow_http=row.allow_http,
            allow_private=row.allow_private,
            resolver=resolver,
        )
    except URLGuardError as exc:
        result: SendClass = (
            "ssrf_refused"
            if exc.code in {"notifications.private_refused", "notifications.http_refused"}
            else "transport"
        )
        return result, None
    sent = dict(headers)
    sent["Host"] = checked.host_header
    if row.hmac_secret is not None:
        sent[SIGNATURE_HEADER] = sign_v1(body, row.hmac_secret)
    sent.update(decode_headers(row))
    request = httpx.Request(
        "POST",
        checked.pinned_url(checked.addresses[0]),
        stream=_BodyStream(body),
        headers=sent,
        extensions={
            "sni_hostname": checked.hostname,
            "timeout": httpx.Timeout(10.0).as_dict(),
        },
    )
    try:
        async with transport_factory() as transport:
            response = await transport.handle_async_request(request)
            response.request = request
            response_status = response.status_code
            try:
                await _discard_body(response)
            finally:
                await response.aclose()
    except httpx.TimeoutException:
        return "timeout", None
    except httpx.TransportError:
        return "transport", None
    if 200 <= response_status < 300:
        return "ok", response_status
    if 300 <= response_status < 400:
        return "redirect_refused", response_status
    return "http_status", response_status
