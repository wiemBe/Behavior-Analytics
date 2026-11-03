"""Alert e-mail end to end (MAIL-1): routing -> template -> a local fake SMTP server.

A dispatch with an administered recipient sends one HTML mail through the real
``EmailNotifier`` and ``smtplib`` to a socket server on 127.0.0.1. Nothing else is
contacted. DB-gated.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.notifiers.channels import EmailNotifier
from app.alerts.routing import RoutingPolicy, default_policy
from app.alerts.service import AlertInput, AlertService
from app.core.config import Settings
from app.models.alert import AlertNotification
from app.models.email import EmailRecipient, EmailTemplate
from app.models.enums import (
    AlertTransition,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from tests.integration.fake_smtp import FakeSmtpServer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"
    ),
]


def _settings(server: FakeSmtpServer, **over: object) -> Settings:
    base: dict[str, object] = {
        "smtp_host": "127.0.0.1",
        "smtp_port": server.port,
        "smtp_security": "none",
        "smtp_from": "alerts@example.internal",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


async def _alert(
    session, *, source_type="log_source", severity=Severity.HIGH, title="Spike <b>x</b>"
):
    result = await AlertService(session).open_or_update(
        AlertInput(
            fingerprint=f"anomaly:{uuid.uuid4().hex[:8]}", title=title,
            severity=severity, source_type=source_type, source_id=uuid.uuid4(),
            evidence={"observed_value": 12.0, "expected_value": 3.0, "deviation_score": 4.5,
                      "reason": "twelve against three"},
            context={"instance": "lab-console"},
        )
    )
    return result.alert


async def _run(session, settings, alert, transition=AlertTransition.OPENED):
    dispatcher = NotificationDispatcher(
        session,
        {NotificationChannel.EMAIL: EmailNotifier(settings)},
        default_policy(settings),
        settings=settings,
    )
    created = await dispatcher.enqueue(alert, transition)
    stats = await dispatcher.dispatch_due()
    return created, stats


async def test_a_stored_recipient_gets_one_html_mail_through_the_relay(
    db_session, monkeypatch
) -> None:
    monkeypatch.delenv("NOTIFY_EMAIL_RECIPIENTS", raising=False)
    db_session.add(EmailRecipient(address="soc@example.org", min_severity="LOW"))
    await db_session.flush()
    alert = await _alert(db_session)
    with FakeSmtpServer() as server:
        created, stats = await _run(db_session, _settings(server), alert)
    assert [n.target for n in created] == ["soc@example.org"]
    assert stats == {"sent": 1, "failed": 0, "dead_letter": 0}
    assert created[0].status == NotificationStatus.SENT
    [received] = server.state.messages
    assert received.recipients == ["soc@example.org"]
    assert received.mail_from == "alerts@example.internal"
    message = received.parsed()
    assert message.get_content_type() == "multipart/alternative"
    assert message["Subject"] == "[HIGH] Spike <b>x</b> (OPENED)"
    html = message.get_body(("html",)).get_content()
    assert "Spike &lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "lab-console" in html and "Log source" in html and "twelve against three" in html
    # The payload row stores a masked copy of the id; the link must use the real one.
    assert f'href="http://localhost:3000/alerts/{alert.id}"' in html
    assert "redacted" not in html
    text = message.get_body(("plain",)).get_content()
    assert "Spike <b>x</b>" in text and "Observed:     12" in text


async def test_recipients_are_filtered_by_severity_and_type_and_deduplicated(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "Env@Example.org, both@example.org")
    db_session.add_all([
        EmailRecipient(address="both@example.org", min_severity="LOW"),
        EmailRecipient(address="critical@example.org", min_severity="CRITICAL"),
        EmailRecipient(address="waf@example.org", min_severity="LOW", alert_types=["waf_endpoint"]),
        EmailRecipient(address="off@example.org", min_severity="LOW", enabled=False),
    ])
    await db_session.flush()
    alert = await _alert(db_session, severity=Severity.HIGH)
    with FakeSmtpServer() as server:
        created, _ = await _run(db_session, _settings(server), alert)
    assert sorted(n.target.lower() for n in created) == [
        "both@example.org", "env@example.org"
    ]
    assert len(server.state.messages) == 2

    waf = await _alert(db_session, source_type="waf_endpoint", severity=Severity.CRITICAL)
    with FakeSmtpServer() as server:
        created, _ = await _run(db_session, _settings(server), waf)
    assert sorted(n.target.lower() for n in created) == [
        "both@example.org", "critical@example.org", "env@example.org", "waf@example.org"
    ]


async def test_the_template_for_the_type_beats_the_default_and_a_disabled_one_falls_back(
    db_session, monkeypatch
) -> None:
    monkeypatch.delenv("NOTIFY_EMAIL_RECIPIENTS", raising=False)
    db_session.add(EmailRecipient(address="soc@example.org", min_severity="LOW"))
    db_session.add_all([
        EmailTemplate(alert_type=None, subject="DEFAULT {{ alert.title }}",
                      html_body="<p>default {{ alert.type }}</p>"),
        EmailTemplate(alert_type="waf_endpoint", subject="WAF {{ alert.title }}",
                      html_body="<p>waf {{ alert.type }}</p>", text_body="waf text"),
        EmailTemplate(alert_type="ips_entity", subject="IPS {{ alert.title }}",
                      html_body="<p>ips</p>", enabled=False),
    ])
    await db_session.flush()
    expected = {
        "waf_endpoint": ("WAF t", "waf WAF endpoint"),
        "ips_entity": ("DEFAULT t", "default IPS entity"),
        "log_source": ("DEFAULT t", "default Log source"),
    }
    for source_type, (subject, body) in expected.items():
        alert = await _alert(db_session, source_type=source_type, title="t")
        with FakeSmtpServer() as server:
            await _run(db_session, _settings(server), alert)
        [received] = server.state.messages
        message = received.parsed()
        assert message["Subject"] == subject, source_type
        assert message.get_body(("html",)).get_content().strip() == f"<p>{body}</p>"
    # The own text part is used as given; a template without one derives it from the HTML.
    assert message.get_body(("plain",)).get_content().strip() == "default Log source"


async def test_with_no_stored_template_the_shipped_default_is_sent(
    db_session, monkeypatch
) -> None:
    monkeypatch.delenv("NOTIFY_EMAIL_RECIPIENTS", raising=False)
    db_session.add(EmailRecipient(address="soc@example.org", min_severity="LOW"))
    await db_session.flush()
    alert = await _alert(db_session)
    with FakeSmtpServer() as server:
        await _run(db_session, _settings(server), alert)
    html = server.state.messages[0].parsed().get_body(("html",)).get_content()
    assert 'width="600"' in html and "Open the alert" in html


async def test_the_recipients_and_templates_are_read_once_per_dispatcher(
    db_session, monkeypatch
) -> None:
    monkeypatch.delenv("NOTIFY_EMAIL_RECIPIENTS", raising=False)
    db_session.add(EmailRecipient(address="soc@example.org", min_severity="LOW"))
    await db_session.flush()
    settings = Settings(smtp_host="127.0.0.1", smtp_security="none")
    dispatcher = NotificationDispatcher(db_session, {}, default_policy(settings), settings=settings)
    alerts = [await _alert(db_session) for _ in range(4)]

    from sqlalchemy import event

    statements: list[str] = []

    def count(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        if "email_recipient" in statement:
            statements.append(statement)

    engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", count)
    try:
        for alert in alerts:
            await dispatcher.enqueue(alert, AlertTransition.OPENED)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(statements) == 1


async def test_a_hand_built_policy_reads_no_stored_recipient(db_session, monkeypatch) -> None:
    db_session.add(EmailRecipient(address="soc@example.org", min_severity="LOW"))
    await db_session.flush()
    alert = await _alert(db_session)
    dispatcher = NotificationDispatcher(db_session, {}, RoutingPolicy())
    assert await dispatcher.enqueue(alert, AlertTransition.OPENED) == []


async def test_an_unconfigured_relay_parks_the_notification_with_the_reason(
    db_session, monkeypatch
) -> None:
    monkeypatch.delenv("NOTIFY_EMAIL_RECIPIENTS", raising=False)
    db_session.add(EmailRecipient(address="soc@example.org", min_severity="LOW"))
    await db_session.flush()
    alert = await _alert(db_session)
    settings = Settings(smtp_host="")
    created, stats = await _run(db_session, settings, alert)
    assert stats["dead_letter"] == 1
    assert created[0].status == NotificationStatus.DEAD_LETTER
    assert created[0].error_message == "SMTP host not configured"


async def test_a_refused_recipient_is_a_dead_letter_without_the_relay_text(
    db_session, monkeypatch
) -> None:
    monkeypatch.delenv("NOTIFY_EMAIL_RECIPIENTS", raising=False)
    db_session.add(EmailRecipient(address="gone@example.org", min_severity="LOW"))
    await db_session.flush()
    alert = await _alert(db_session)
    with FakeSmtpServer() as server:
        server.state.refuse_rcpt = True
        created, stats = await _run(db_session, _settings(server), alert)
    assert stats["dead_letter"] == 1
    assert created[0].error_message == "SMTP delivery failed: recipient_refused"
    count = await db_session.scalar(select(func.count()).select_from(AlertNotification))
    assert count == 1
