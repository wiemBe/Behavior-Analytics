"""Notifier channels. No real external notification is ever sent:
HTTP channels are mocked with respx; syslog's socket is monkeypatched."""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.alerts.notifiers.base import MockNotifier, NotificationMessage, NotifierError
from app.alerts.notifiers.channels import (
    GenericWebhookNotifier,
    SlackNotifier,
    SyslogNotifier,
    TeamsNotifier,
)
from app.alerts.notifiers.registry import build_notifiers
from app.models.enums import AlertTransition, NotificationChannel, Severity
from app.schemas.alert import notification_error_message

pytestmark = pytest.mark.asyncio


def _msg() -> NotificationMessage:
    return NotificationMessage(
        title="VOLUME_DROP on fw-01",
        body="average EPS below baseline",
        severity=Severity.HIGH,
        transition=AlertTransition.OPENED,
        alert_id="a1",
        fingerprint="anomaly:abc",
        fields={"observed_value": 3.0, "expected_value": 50.0},
    )


async def test_mock_notifier_records_and_can_fail() -> None:
    n = MockNotifier(NotificationChannel.SLACK, fail_times=1)
    with pytest.raises(NotifierError):
        await n.send("hook", _msg())      # first attempt fails
    await n.send("hook", _msg())          # second succeeds
    assert len(n.sent) == 1


@respx.mock
async def test_slack_posts_blocks() -> None:
    route = respx.post("https://hooks.slack.test/x").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await SlackNotifier(client).send("https://hooks.slack.test/x", _msg())
    assert route.called
    body = route.calls[0].request.content
    assert b"blocks" in body


@respx.mock
async def test_teams_posts_messagecard() -> None:
    route = respx.post("https://teams.test/hook").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await TeamsNotifier(client).send("https://teams.test/hook", _msg())
    assert b"MessageCard" in route.calls[0].request.content


@respx.mock
async def test_generic_webhook_5xx_is_retryable() -> None:
    respx.post("https://hook.test/x").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(NotifierError) as exc:
            await GenericWebhookNotifier(client).send("https://hook.test/x", _msg())
    assert not exc.value.permanent  # 5xx -> retry


@respx.mock
async def test_generic_webhook_4xx_is_permanent() -> None:
    respx.post("https://hook.test/x").mock(return_value=httpx.Response(400))
    async with httpx.AsyncClient() as client:
        with pytest.raises(NotifierError) as exc:
            await GenericWebhookNotifier(client).send("https://hook.test/x", _msg())
    assert exc.value.permanent  # 4xx -> do not retry


async def test_http_transport_error_does_not_expose_webhook_url() -> None:
    target = "https://hooks.example.test/secret-path?token=credential"

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"could not connect to {request.url}", request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(NotifierError) as exc:
            await GenericWebhookNotifier(client).send(target, _msg())

    assert str(exc.value) == "transport error: ConnectError"
    assert target not in str(exc.value)
    assert "secret-path" not in str(exc.value)
    assert "credential" not in str(exc.value)


@pytest.mark.parametrize(
    ("channel", "target", "error_message", "expected"),
    [
        (
            NotificationChannel.TEAMS,
            "https://hooks.example.test/SECRET?token=credential",
            (
                "first https://hooks.example.test/SECRET?token=credential; "
                "second https://hooks.example.test/SECRET?token=credential"
            ),
            "first https://hooks.example.test; second https://hooks.example.test",
        ),
        (
            NotificationChannel.SLACK,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error for /SECRET?token=credential",
            "transport error for https://hooks.example.test",
        ),
        (
            NotificationChannel.WEBHOOK,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error for /SECRET",
            "transport error for https://hooks.example.test",
        ),
        (
            NotificationChannel.WEBHOOK,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error for token=credential",
            "transport error for https://hooks.example.test",
        ),
        (
            NotificationChannel.WEBHOOK,
            "https://hooks.example.test/SECRET?token=credential",
            "transport error: ConnectError",
            "transport error: ConnectError",
        ),
        (
            NotificationChannel.EMAIL,
            "analyst@example.test",
            "delivery failed for analyst@example.test",
            "delivery failed for analyst@example.test",
        ),
        (
            NotificationChannel.SYSLOG,
            "logs.example.test:6514",
            "delivery failed for logs.example.test:6514",
            "delivery failed for logs.example.test:6514",
        ),
        (
            NotificationChannel.WEBHOOK,
            "https://hooks.example.test/SECRET?token=credential",
            None,
            None,
        ),
    ],
)
async def test_notification_error_message_redacts_legacy_http_targets(
    channel: NotificationChannel,
    target: str,
    error_message: str | None,
    expected: str | None,
) -> None:
    assert notification_error_message(channel, target, error_message) == expected


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
async def test_redirect_is_a_permanent_failure(status_code: int) -> None:
    target = "https://hooks.example/secret-path?token=credential"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            status_code,
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(NotifierError) as exc:
            await GenericWebhookNotifier(client).send(target, _msg())

    assert exc.value.permanent is True
    assert str(exc.value) == f"permanent status {status_code}"
    assert seen == [target]
    text = str(exc.value)
    assert "secret-path" not in text
    assert "credential" not in text
    assert "169.254.169.254" not in text


async def test_gzip_body_is_capped_raw_and_not_decompressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The probe's plain body is 100000 B's. Stored (uncompressed) gzip keeps the
    # magic header and is larger than the 64 KiB raw cap, so a decoded read
    # would pull the whole stream and a capped raw read stops at 64 KiB.
    blob = gzip.compress(b"B" * 100_000, compresslevel=0)
    assert blob.startswith(b"\x1f\x8b")
    assert len(blob) > 64 * 1024

    class CountingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.pulled = 0
            self.closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            position = 0
            while position < len(blob):
                chunk = blob[position : position + 8 * 1024]
                position += len(chunk)
                self.pulled += len(chunk)
                yield chunk

        async def aclose(self) -> None:
            self.closed = True

    stream = CountingStream()

    async def decoded_read(self: httpx.Response, *_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("env channel decoded the response body")

    async def decoded_chunks(
        self: httpx.Response, chunk_size: int | None = None
    ) -> AsyncIterator[bytes]:
        raise AssertionError("env channel decoded the response body")
        yield b""

    monkeypatch.setattr(httpx.Response, "aread", decoded_read)
    monkeypatch.setattr(httpx.Response, "aiter_bytes", decoded_chunks)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=stream, request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await GenericWebhookNotifier(client).send("https://hooks.example/gzip-bomb", _msg())

    assert stream.closed is True
    assert stream.pulled == 64 * 1024
    assert stream.pulled < len(blob)


async def test_env_channel_client_ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    notifiers = build_notifiers()
    client = notifiers[NotificationChannel.SLACK]._client
    try:
        assert client.trust_env is False
        assert client.follow_redirects is False
        transport = client._transport_for_url(httpx.URL("https://hooks.example/secret-path"))
        assert type(transport._pool).__name__ == "AsyncConnectionPool"
    finally:
        await client.aclose()


async def test_syslog_sends_udp_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[bytes, tuple]] = []

    class FakeSock:
        def __init__(self, *a, **k) -> None: ...
        def sendto(self, data: bytes, addr: tuple) -> None:
            sent.append((data, addr))
        def close(self) -> None: ...

    monkeypatch.setattr("socket.socket", lambda *a, **k: FakeSock())
    await SyslogNotifier().send("logs.internal:514", _msg())
    assert sent
    data, addr = sent[0]
    assert addr == ("logs.internal", 514)
    assert b"qradar-observability" in data
