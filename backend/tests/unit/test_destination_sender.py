"""Named-destination send: real framing, the deadline, and stored-header refusal.

The socket test binds 127.0.0.1 only. Nothing here resolves or dials a public host.
"""

from __future__ import annotations

import asyncio
import json

import h11
import httpx
import pytest

from app.alerts.notifiers.destination_sender import (
    StoredHeadersInvalid,
    http_transport,
    send_to_destination,
)
from app.alerts.notifiers.webhook_v1 import sign_v1
from app.core.config import Settings
from app.models.notification_destination import NotificationDestination

PUBLIC = "93.184.216.34"
SECRET = "wire-secret-58"
BODY = b'{"schema_version":1,"event":"test"}'


def _row(**over: object) -> NotificationDestination:
    values: dict[str, object] = {
        "name": "wire",
        "enabled": True,
        "url": "https://hooks.example/hook",
        "allow_http": False,
        "allow_private": False,
        "hmac_secret": SECRET,
        "headers": "{}",
        "created_by": "t",
        "updated_by": "t",
    }
    values.update(over)
    return NotificationDestination(**values)  # type: ignore[arg-type]


def _public(host: str, port: int) -> tuple[str, ...]:
    assert host == "hooks.example"
    return (PUBLIC,)


def _headers() -> dict[str, str]:
    return {"Content-Type": "application/json", "Idempotency-Key": "event-key"}


async def _read_http(reader: asyncio.StreamReader) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = await reader.read(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    while len(rest) < length:
        chunk = await reader.read(length - len(rest))
        if not chunk:
            break
        rest += chunk
    return head + b"\r\n\r\n" + rest


async def test_http_transport_writes_content_length_and_the_signed_body() -> None:
    """The real transport, against a local socket. This is the check SEC12-03 missed."""
    loop = asyncio.get_running_loop()
    captured: asyncio.Future[bytes] = loop.create_future()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(_read_http(reader), timeout=2)
        except TimeoutError:
            raw = b""
        if not captured.done():
            captured.set_result(raw)
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    sockets = server.sockets
    assert sockets is not None
    port = sockets[0].getsockname()[1]
    row = _row(
        url=f"http://hooks.example:{port}/hook?tenant=blue",
        allow_http=True,
        allow_private=True,
        headers=json.dumps({"X-SOC-Key": "hv-58"}),
    )

    def resolve(host: str, resolved_port: int) -> tuple[str, ...]:
        assert host == "hooks.example" and resolved_port == port
        return ("127.0.0.1",)

    try:
        result = await asyncio.wait_for(
            send_to_destination(
                row,
                BODY,
                _headers(),
                resolver=resolve,
                transport_factory=http_transport,
                deadline_seconds=5,
            ),
            timeout=5,
        )
        raw = await asyncio.wait_for(captured, timeout=5)
    finally:
        server.close()
        await server.wait_closed()

    assert result == ("ok", 204)
    head, _, received = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"POST /hook?tenant=blue HTTP/1.1")
    assert received == BODY
    parsed = {
        line.split(b":", 1)[0].decode().lower(): line.split(b":", 1)[1].strip().decode()
        for line in head.split(b"\r\n")[1:]
        if b":" in line
    }
    assert parsed["content-length"] == str(len(BODY))
    assert "transfer-encoding" not in parsed
    assert parsed["x-qradar-dash-signature"] == sign_v1(BODY, SECRET)
    assert parsed["x-soc-key"] == "hv-58"
    assert parsed["host"] == f"hooks.example:{port}"


class _Stall(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(204)


async def test_one_deadline_stops_a_stalled_send_before_the_idle_timeout() -> None:
    started = asyncio.get_running_loop().time()
    result = await send_to_destination(
        _row(),
        BODY,
        _headers(),
        resolver=_public,
        transport_factory=_Stall,
        deadline_seconds=0.2,
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert result == ("timeout", None)
    assert elapsed < 1


async def test_the_deadline_setting_is_what_bounds_the_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.alerts.notifiers.destination_sender.get_settings",
        lambda: Settings(notify_send_deadline_seconds=0.2),
    )
    started = asyncio.get_running_loop().time()
    result = await send_to_destination(
        _row(), BODY, _headers(), resolver=_public, transport_factory=_Stall
    )
    assert result == ("timeout", None)
    assert asyncio.get_running_loop().time() - started < 1


async def test_idle_timeouts_stay_ten_seconds_under_the_deadline() -> None:
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(204)

    result = await send_to_destination(
        _row(),
        BODY,
        _headers(),
        resolver=_public,
        transport_factory=lambda: httpx.MockTransport(handler),
        deadline_seconds=5,
    )
    assert result == ("ok", 204)
    assert seen == [{"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}]


class _Protocol(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise h11.LocalProtocolError("Too much data for declared Content-Length")


async def test_a_protocol_error_is_a_transport_failure_not_an_exception() -> None:
    result = await send_to_destination(
        _row(),
        BODY,
        _headers(),
        resolver=_public,
        transport_factory=_Protocol,
    )
    assert result == ("transport", None)


@pytest.mark.parametrize(
    "stored",
    [
        {"Transfer-Encoding": "chunked"},
        {"Connection": "keep-alive"},
        {"TE": "trailers"},
        {"Upgrade": "websocket"},
        {"Expect": "100-continue"},
        {"Host": "evil.example"},
        {"X-QRadar-Dash-Signature": "v1=forged"},
        {"X-SOC-Key": "value\r\nX-Injected: yes"},
        {"X-SOC-Key": "has\na-break"},
        {"X-SOC-Key": "has\x00a-nul"},
        {"Not A Name": "x"},
    ],
)
async def test_a_stored_header_the_schema_would_refuse_never_reaches_the_transport(
    stored: dict[str, str],
) -> None:
    def no_transport() -> httpx.AsyncBaseTransport:
        raise AssertionError("invalid stored headers must not reach the transport")

    with pytest.raises(StoredHeadersInvalid, match="payload_invalid"):
        await send_to_destination(
            _row(headers=json.dumps(stored)),
            BODY,
            _headers(),
            resolver=_public,
            transport_factory=no_transport,
        )


async def test_malformed_stored_headers_are_payload_invalid() -> None:
    def no_transport() -> httpx.AsyncBaseTransport:
        raise AssertionError("malformed headers must not be sent")

    with pytest.raises(StoredHeadersInvalid):
        await send_to_destination(
            _row(headers="[]"),
            BODY,
            _headers(),
            resolver=_public,
            transport_factory=no_transport,
        )
