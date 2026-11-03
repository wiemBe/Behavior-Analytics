"""Build the channel -> Notifier map from configuration.

Kept separate from the notifiers themselves so the dispatcher can be handed a
map without importing httpx/smtplib at module load, and so tests can substitute
MockNotifiers wholesale.
"""

from __future__ import annotations

import httpx

from app.alerts.notifiers.base import Notifier
from app.alerts.notifiers.channels import (
    EmailNotifier,
    GenericWebhookNotifier,
    SlackNotifier,
    SyslogNotifier,
    TeamsNotifier,
)
from app.core.config import Settings
from app.models.enums import NotificationChannel


def build_notifiers(
    client: httpx.AsyncClient | None = None, settings: Settings | None = None
) -> dict[NotificationChannel, Notifier]:
    client = client or httpx.AsyncClient()
    return {
        NotificationChannel.TEAMS: TeamsNotifier(client),
        NotificationChannel.SLACK: SlackNotifier(client),
        NotificationChannel.WEBHOOK: GenericWebhookNotifier(client),
        NotificationChannel.SYSLOG: SyslogNotifier(),
        # Read through Settings at send time (MAIL-1), never os.environ here.
        NotificationChannel.EMAIL: EmailNotifier(settings),
    }
