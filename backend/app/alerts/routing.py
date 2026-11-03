"""Notification routing.

A RoutingPolicy decides which channels+targets an alert notification goes to,
matched on severity, alert type (condition family from the fingerprint),
log-source owner, instance and source class. This is deliberately data-driven so operators can
route "critical anomalies on owner=payments → Teams+PagerDuty webhook" without
code changes.

The default policy is derived from configured channel credentials in Settings
and a minimum severity, so a fresh deployment routes everything above LOW to
whatever webhooks are configured.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.models.alert import Alert
from app.models.enums import NotificationChannel, Severity

_SEVERITY_RANK = {
    Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
    Severity.HIGH: 3, Severity.CRITICAL: 4,
}


@dataclass(frozen=True)
class Route:
    channel: NotificationChannel
    target: str


@dataclass
class RoutingRule:
    channel: NotificationChannel
    target: str
    min_severity: Severity = Severity.LOW
    # Match conditions (None = match any).
    alert_types: frozenset[str] | None = None      # e.g. {"anomaly", "search_threshold"}
    owners: frozenset[str] | None = None
    instances: frozenset[str] | None = None
    # Phase C: the log-source class (e.g. {"FIREWALL"}) carried in the alert
    # context. A rule with classes never matches an alert that has none, so an
    # unclassified or non-source alert cannot slip into a class-scoped route.
    source_classes: frozenset[str] | None = None
    # MAIL-1: the alert's own type (``alert.source_type``: log_source, waf_endpoint,
    # ...), distinct from ``alert_types`` above, which matches the fingerprint's
    # condition family. A stored e-mail recipient scopes on this one.
    source_types: frozenset[str] | None = None

    def matches(self, alert: Alert) -> bool:
        if _SEVERITY_RANK[alert.severity] < _SEVERITY_RANK[self.min_severity]:
            return False
        if self.alert_types is not None:
            condition = alert.fingerprint.split(":", 1)[0]
            if condition not in self.alert_types:
                return False
        if self.source_types is not None and alert.source_type not in self.source_types:
            return False
        if self.owners is not None:
            owner = (alert.context or {}).get("owner")
            if owner not in self.owners:
                return False
        if self.instances is not None:
            instance = (alert.context or {}).get("instance")
            if instance not in self.instances:
                return False
        if self.source_classes is not None:
            source_class = (alert.context or {}).get("source_class")
            if source_class not in self.source_classes:
                return False
        return True


@dataclass
class RoutingPolicy:
    rules: list[RoutingRule] = field(default_factory=list)
    # MAIL-1. True only for the production policy (``default_policy``): the
    # dispatcher then loads the administered e-mail recipients once per
    # dispatcher (one query, never one per alert) and resolves them beside
    # ``rules``. A hand-built policy leaves it off and touches no table.
    include_stored_recipients: bool = False

    def resolve(self, alert: Alert, extra_rules: Sequence[RoutingRule] = ()) -> list[Route]:
        seen: set[tuple[str, str]] = set()
        routes: list[Route] = []
        for rule in [*self.rules, *extra_rules]:
            if rule.matches(alert):
                # An address is one mailbox whatever its case: the env list and a
                # stored row naming it twice send one mail.
                target = (
                    rule.target.lower()
                    if rule.channel is NotificationChannel.EMAIL
                    else rule.target
                )
                key = (rule.channel.value, target)
                if key not in seen:
                    seen.add(key)
                    routes.append(Route(channel=rule.channel, target=rule.target))
        return routes


def env_email_recipients() -> list[str]:
    """The comma-separated ``NOTIFY_EMAIL_RECIPIENTS`` list, blanks dropped."""
    import os

    return [
        item.strip()
        for item in os.environ.get("NOTIFY_EMAIL_RECIPIENTS", "").split(",")
        if item.strip()
    ]


def default_policy(settings: Settings | None = None) -> RoutingPolicy:
    """Build a policy from configured channel credentials.

    Reads the webhook/email/syslog settings. A channel with no configured target
    contributes no rule, so unconfigured channels are simply never routed to.
    """
    import os

    settings = settings or get_settings()
    rules: list[RoutingRule] = []

    def _add(channel: NotificationChannel, target: str | None) -> None:
        if target:
            rules.append(RoutingRule(channel=channel, target=target, min_severity=Severity.LOW))

    _add(NotificationChannel.TEAMS, os.environ.get("NOTIFY_TEAMS_WEBHOOK_URL"))
    _add(NotificationChannel.SLACK, os.environ.get("NOTIFY_SLACK_WEBHOOK_URL"))
    _add(NotificationChannel.WEBHOOK, os.environ.get("NOTIFY_GENERIC_WEBHOOK_URL"))
    # One rule per recipient, so delivery to one mailbox failing does not park the
    # notification for the others. Administered recipients (MAIL-1) join these at
    # dispatch; this env list keeps working and the API shows it read-only.
    for recipient in env_email_recipients():
        _add(NotificationChannel.EMAIL, recipient)
    syslog_host = os.environ.get("NOTIFY_SYSLOG_HOST")
    if syslog_host:
        syslog_port = os.environ.get("NOTIFY_SYSLOG_PORT", "514")
        _add(NotificationChannel.SYSLOG, f"{syslog_host}:{syslog_port}")
    return RoutingPolicy(rules=rules, include_stored_recipients=True)
