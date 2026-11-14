"""SEC-RO2 §2: a webhook never targets a QRadar console, and never carries its credentials.

Named destinations are checked on create, patch, test send and delivery; the environment routes
(Teams, Slack, generic webhook) at delivery. A console registered after the destination counts.
The transport is a mock and the resolver a stub: nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.base import MockNotifier
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.api.routes import notification_destinations as routes
from app.core.rate_limit import limiter
from app.models.alert import AlertNotification
from app.models.enums import AlertTransition, NotificationChannel, NotificationStatus
from app.models.instance import QRadarInstance
from tests.integration.test_fleet_api import ADMIN, Client, run
from tests.integration.test_notification_destination_delivery import (
    _alert,
    _destination,
    _resolver,
    _rule,
    _transport,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

BASE = "/api/v1/admin/notifications/destinations"
PUBLIC = "93.184.216.34"
CONSOLE = "https://qradar-console.example.net"


@pytest.fixture(autouse=True)
def _network_free(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    limiter.reset()
    monkeypatch.setattr(routes, "_resolver", lambda host, port: (PUBLIC,))
    yield
    limiter.reset()


def _add_console(host: str = CONSOLE, mcp: str | None = None) -> None:
    async def work(session):  # type: ignore[no-untyped-def]
        session.add(
            QRadarInstance(
                id=uuid.uuid4(),
                name=f"c-{uuid.uuid4().hex[:6]}",
                console_host=host,
                mcp_base_url=mcp,
                provider_kind="mock",
            )
        )

    run(work)


def _post(client: Client, **over: object):  # type: ignore[no-untyped-def]
    body: dict[str, object] = {"name": "SOAR", "url": "https://hooks.example/x"}
    body.update(over)
    return client.post(BASE, json=body)


# ------------------------------------------------------------------ headers
@pytest.mark.parametrize("name", ["SEC", "sec", "Authorization", "AUTHORIZATION",
                                  "Proxy-Authorization", "Cookie", "cOOkie"])
def test_reserved_header_names_are_refused_on_create_and_patch(db_schema, name: str) -> None:
    client = Client(ADMIN)
    secret = "do-not-echo-1f3a"
    response = _post(client, headers={name: secret})
    assert response.status_code == 422
    assert secret not in response.text
    created = _post(client, name="ok")
    assert created.status_code == 201
    patched = client.patch(f"{BASE}/{created.json()['id']}", json={"headers": {name: secret}})
    assert patched.status_code == 422
    assert secret not in patched.text


async def test_a_stored_reserved_header_is_never_sent(db_session) -> None:
    d = await _destination(db_session, headers=json.dumps({"SEC": "token-9"}))
    await _rule(db_session, d, stop_default=True)
    alert = await _alert(db_session)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    disp = NotificationDispatcher(
        db_session, {}, RoutingPolicy(rules=[]),
        destination_resolver=_resolver, destination_transport=_transport(handler),
    )
    [row] = await disp.enqueue(alert, AlertTransition.OPENED)
    await disp.dispatch_due()
    assert sent == []
    assert row.status == NotificationStatus.DEAD_LETTER
    assert row.error_message == "payload_invalid"


# ------------------------------------------------------------- console host
@pytest.mark.parametrize(
    "url",
    [
        CONSOLE + "/hook",
        "https://QRADAR-CONSOLE.example.net./hook",
        "https://qradar-console.example.net:8443/hook",
    ],
)
def test_a_destination_aimed_at_a_console_is_refused_on_create_patch_and_test(
    db_schema, url: str
) -> None:
    _add_console()
    client = Client(ADMIN)
    refused = _post(client, url=url)
    assert refused.status_code == 422
    assert refused.json()["detail_code"] == "notifications.console_refused"
    assert "qradar-console" not in refused.text.lower()
    ok = _post(client, name="fine")
    assert ok.status_code == 201
    patched = client.patch(f"{BASE}/{ok.json()['id']}", json={"url": url})
    assert patched.status_code == 422
    assert patched.json()["detail_code"] == "notifications.console_refused"


def test_a_literal_ip_console_and_the_mcp_host_are_refused(db_schema) -> None:
    _add_console("https://10.1.2.3", mcp="http://mcp-sidecar.internal:5000")
    client = Client(ADMIN)
    for url in ("https://10.1.2.3/x", "https://[::ffff:10.1.2.3]/x",
                "https://mcp-sidecar.internal/x"):
        response = _post(client, url=url, allow_private=True, allow_http=True)
        assert response.status_code == 422, url
        assert response.json()["detail_code"] == "notifications.console_refused"
    assert _post(client, url="https://10.1.2.4/x", allow_private=True).status_code == 201


def test_a_console_registered_after_the_destination_blocks_the_test_send(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Client(ADMIN)
    created = _post(client, url=CONSOLE + "/hook", name="late")
    assert created.status_code == 201  # no console registered yet
    _add_console()
    wire: list[httpx.Request] = []
    monkeypatch.setattr(
        routes, "_http_transport",
        lambda: httpx.MockTransport(lambda r: wire.append(r) or httpx.Response(204)),
    )
    result = client.post(f"{BASE}/{created.json()['id']}/test")
    assert result.status_code == 200
    assert result.json()["result"] == "ssrf_refused" and wire == []


async def test_delivery_refuses_a_destination_whose_console_was_added_later(db_session) -> None:
    d = await _destination(db_session, url=CONSOLE + "/hook", headers="{}")
    await _rule(db_session, d, stop_default=True)
    alert = await _alert(db_session)
    wire: list[httpx.Request] = []
    disp = NotificationDispatcher(
        db_session, {}, RoutingPolicy(rules=[]),
        destination_resolver=_resolver,
        destination_transport=_transport(lambda r: wire.append(r) or httpx.Response(204)),
    )
    [row] = await disp.enqueue(alert, AlertTransition.OPENED)
    db_session.add(QRadarInstance(id=uuid.uuid4(), name="late", console_host=CONSOLE,
                                  provider_kind="mock"))
    await db_session.flush()
    await disp.dispatch_due()
    assert wire == []
    assert row.status == NotificationStatus.DEAD_LETTER
    assert "qradar-console" not in (row.error_message or "")


@pytest.mark.parametrize(
    "channel", [NotificationChannel.TEAMS, NotificationChannel.SLACK, NotificationChannel.WEBHOOK]
)
async def test_env_routes_aimed_at_a_console_are_refused_at_send(
    db_session, channel: NotificationChannel
) -> None:
    db_session.add(QRadarInstance(id=uuid.uuid4(), name="c", console_host=CONSOLE,
                                  provider_kind="mock"))
    await db_session.flush()
    notifier = MockNotifier(channel)
    policy = RoutingPolicy(rules=[RoutingRule(channel=channel, target=CONSOLE + "/api/hook")])
    disp = NotificationDispatcher(db_session, {channel: notifier}, policy)
    alert = await _alert(db_session)
    await disp.enqueue(alert, AlertTransition.OPENED)
    stats = await disp.dispatch_due()
    assert notifier.sent == [] and stats["dead_letter"] == 1
    row = await db_session.scalar(select(AlertNotification))
    assert row.status == NotificationStatus.DEAD_LETTER
    assert "qradar-console" not in (row.error_message or "")


async def test_env_route_to_another_host_is_still_sent(db_session) -> None:
    db_session.add(QRadarInstance(id=uuid.uuid4(), name="c", console_host=CONSOLE,
                                  provider_kind="mock"))
    await db_session.flush()
    notifier = MockNotifier(NotificationChannel.SLACK)
    policy = RoutingPolicy(rules=[RoutingRule(channel=NotificationChannel.SLACK,
                                              target="https://hooks.slack.example/T/B")])
    disp = NotificationDispatcher(db_session, {NotificationChannel.SLACK: notifier}, policy)
    await disp.enqueue(await _alert(db_session), AlertTransition.OPENED)
    assert (await disp.dispatch_due())["sent"] == 1


