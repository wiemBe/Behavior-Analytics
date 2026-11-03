"""Routing of administered e-mail recipients (MAIL-1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.alerts.routing import RoutingPolicy, RoutingRule, default_policy, env_email_recipients
from app.models.alert import Alert
from app.models.enums import AlertStatus, NotificationChannel, Severity

EMAIL = NotificationChannel.EMAIL


def _alert(severity: Severity = Severity.HIGH, source_type: str = "log_source") -> Alert:
    now = datetime.now(UTC)
    return Alert(
        dedup_key="k", fingerprint="anomaly:abc", title="t", severity=severity,
        status=AlertStatus.OPEN, source_type=source_type, source_id=uuid.uuid4(),
        opened_at=now, first_seen_at=now, context={},
    )


def _rule(address: str, *, minimum: Severity = Severity.LOW, types=None) -> RoutingRule:
    return RoutingRule(
        channel=EMAIL, target=address, min_severity=minimum,
        source_types=frozenset(types) if types is not None else None,
    )


def _targets(policy: RoutingPolicy, alert: Alert, extra: list[RoutingRule]) -> set[str]:
    return {r.target for r in policy.resolve(alert, extra) if r.channel is EMAIL}


def test_a_stored_recipient_is_routed_by_severity() -> None:
    extra = [_rule("high@example.org", minimum=Severity.HIGH), _rule("all@example.org")]
    policy = RoutingPolicy()
    assert _targets(policy, _alert(Severity.CRITICAL), extra) == {
        "high@example.org", "all@example.org"
    }
    assert _targets(policy, _alert(Severity.HIGH), extra) == {
        "high@example.org", "all@example.org"
    }
    assert _targets(policy, _alert(Severity.MEDIUM), extra) == {"all@example.org"}


def test_a_stored_recipient_is_routed_by_alert_type_and_null_means_all() -> None:
    extra = [
        _rule("waf@example.org", types={"waf_endpoint", "ips_entity"}),
        _rule("any@example.org"),
    ]
    policy = RoutingPolicy()
    assert _targets(policy, _alert(source_type="waf_endpoint"), extra) == {
        "waf@example.org", "any@example.org"
    }
    assert _targets(policy, _alert(source_type="log_source"), extra) == {"any@example.org"}
    # The type filter is the alert's own type, not the fingerprint's condition family.
    only_anomaly = [RoutingRule(channel=EMAIL, target="x@example.org",
                                alert_types=frozenset({"anomaly"}))]
    assert _targets(policy, _alert(), only_anomaly) == {"x@example.org"}


def test_an_empty_type_list_matches_nothing_rather_than_everything() -> None:
    assert _targets(RoutingPolicy(), _alert(), [_rule("none@example.org", types=set())]) == set()


def test_env_and_stored_recipients_work_together_and_one_address_gets_one_mail(
    monkeypatch,
) -> None:
    monkeypatch.setenv("NOTIFY_EMAIL_RECIPIENTS", "Env@Example.org, shared@example.org")
    policy = default_policy()
    assert policy.include_stored_recipients is True
    routes = policy.resolve(_alert(), [_rule("shared@EXAMPLE.org"), _rule("db@example.org")])
    mails = [r.target for r in routes if r.channel is EMAIL]
    assert sorted(t.lower() for t in mails) == [
        "db@example.org", "env@example.org", "shared@example.org"
    ]
    assert len(mails) == 3
    assert env_email_recipients() == ["Env@Example.org", "shared@example.org"]


def test_a_hand_built_policy_does_not_ask_for_stored_recipients() -> None:
    assert RoutingPolicy().include_stored_recipients is False
    assert RoutingPolicy(rules=[_rule("a@example.org")]).resolve(_alert()) == RoutingPolicy(
        rules=[_rule("a@example.org")]
    ).resolve(_alert(), [])
