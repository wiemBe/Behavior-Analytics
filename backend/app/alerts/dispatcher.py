"""Notification dispatch: enqueue on transition, then deliver with retry.

Delivery model:
  * `enqueue(alert, transition)` creates one AlertNotification per routed
    (channel, target). The unique constraint on (alert, transition, channel,
    target) means a repeated transition cannot enqueue a duplicate — the second
    insert is caught and skipped. Resolve notifications are only enqueued when
    recovery notifications are enabled.
  * `dispatch_due(...)` picks PENDING/FAILED rows whose next_attempt_at has
    passed, sends them via the channel's Notifier, and records the outcome:
      - success            -> SENT (delivery audit trail: sent_at, attempts)
      - retryable failure  -> FAILED, next_attempt_at = now + backoff(jitter)
      - permanent failure
        or retries exhausted -> DEAD_LETTER (parked for inspection)

Clock, sleep and RNG are injectable; the notifier map is injected so tests use
MockNotifiers and no real notification is ever sent under test.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notifiers.base import NotificationMessage, Notifier, NotifierError
from app.alerts.query import alert_family
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.core.config import Settings, get_settings
from app.models.alert import Alert, AlertNotification
from app.models.email import EmailRecipient, EmailTemplate
from app.models.enums import (
    AlertStatus,
    AlertTransition,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from app.security.redaction import redact
from app.services.backoff import backoff_delay


class AlertEnqueuer(Protocol):
    """What the detection engines need from a dispatcher: record the intent to
    notify. Structural, so a test double needs no inheritance and the engines
    never depend on delivery."""

    async def enqueue(
        self, alert: Alert, transition: AlertTransition
    ) -> list[AlertNotification]: ...


class NotificationDispatcher:
    def __init__(
        self,
        session: AsyncSession,
        notifiers: dict[NotificationChannel, Notifier],
        policy: RoutingPolicy,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.session = session
        self.notifiers = notifiers
        self.policy = policy
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
        # MAIL-1. Loaded on first use and kept for this dispatcher's life, so a run
        # costs one recipient query and one template query however many alerts it
        # routes or delivers.
        self._stored_rules: list[RoutingRule] | None = None
        self._email_templates: list[EmailTemplate] | None = None

    async def _stored_email_rules(self) -> list[RoutingRule]:
        """One routing rule per enabled administered recipient (MAIL-1)."""
        if not self.policy.include_stored_recipients:
            return []
        if self._stored_rules is None:
            rows = (
                await self.session.scalars(
                    select(EmailRecipient)
                    .where(EmailRecipient.enabled.is_(True))
                    .order_by(EmailRecipient.address)
                )
            ).all()
            self._stored_rules = [
                RoutingRule(
                    channel=NotificationChannel.EMAIL,
                    target=row.address,
                    min_severity=Severity(row.min_severity),
                    source_types=(
                        frozenset(row.alert_types) if row.alert_types is not None else None
                    ),
                )
                for row in rows
            ]
        return self._stored_rules

    async def _templates(self) -> list[EmailTemplate]:
        if self._email_templates is None:
            self._email_templates = list(
                (
                    await self.session.scalars(
                        select(EmailTemplate).where(EmailTemplate.enabled.is_(True))
                    )
                ).all()
            )
        return self._email_templates

    async def enqueue(self, alert: Alert, transition: AlertTransition) -> list[AlertNotification]:
        if transition is AlertTransition.RESOLVED and not self.settings.notify_send_recovery:
            return []

        created: list[AlertNotification] = []
        for route in self.policy.resolve(alert, await self._stored_email_rules()):
            payload = self._render(alert, transition)
            notif = AlertNotification(
                alert_id=alert.id,
                channel=route.channel,
                target=route.target,
                transition=transition,
                status=NotificationStatus.PENDING,
                max_attempts=self.settings.notify_max_retries + 1,
                next_attempt_at=self._clock(),
                payload=redact(payload),
            )
            try:
                # SAVEPOINT, not a plain flush: a duplicate on one route must not
                # roll back the rows already enqueued for the other routes in this
                # same transition, nor any work the caller did before enqueueing.
                # The add() belongs inside the savepoint so the failed insert is
                # unwound with it and the session stays usable.
                async with self.session.begin_nested():
                    self.session.add(notif)
                    await self.session.flush()
            except IntegrityError:
                # Duplicate (alert, transition, channel, target): already queued.
                continue
            created.append(notif)
        return created

    async def dispatch_due(self, *, limit: int = 100) -> dict[str, int]:
        now = self._clock()
        due = list(
            (
                await self.session.scalars(
                    select(AlertNotification)
                    .where(
                        AlertNotification.status.in_(
                            [NotificationStatus.PENDING, NotificationStatus.FAILED]
                        ),
                        or_(
                            AlertNotification.next_attempt_at.is_(None),
                            AlertNotification.next_attempt_at <= now,
                        ),
                    )
                    .order_by(AlertNotification.next_attempt_at)
                    .limit(limit)
                )
            ).all()
        )

        stats = {"sent": 0, "failed": 0, "dead_letter": 0}
        for notif in due:
            await self._attempt(notif, stats)
        await self.session.flush()
        return stats

    async def _attempt(self, notif: AlertNotification, stats: dict[str, int]) -> None:
        notifier = self.notifiers.get(notif.channel)
        notif.attempts += 1
        if notifier is None:
            notif.status = NotificationStatus.DEAD_LETTER
            notif.error_message = f"no notifier registered for {notif.channel}"
            stats["dead_letter"] += 1
            return

        message = self._message_from_payload(notif)
        if notif.channel == NotificationChannel.EMAIL:
            from app.services.email_templates import choose_template

            message.template = choose_template(await self._templates(), message.alert_type)
        try:
            await notifier.send(notif.target, message)
        except NotifierError as exc:
            self._handle_failure(notif, exc, stats)
            return
        except Exception as exc:
            self._handle_failure(notif, NotifierError(str(exc)), stats)
            return

        notif.status = NotificationStatus.SENT
        notif.sent_at = self._clock()
        notif.error_message = None
        stats["sent"] += 1

    def _handle_failure(
        self, notif: AlertNotification, exc: NotifierError, stats: dict[str, int]
    ) -> None:
        notif.error_message = str(exc)[:2000]
        exhausted = notif.attempts >= notif.max_attempts
        if exc.permanent or exhausted:
            notif.status = NotificationStatus.DEAD_LETTER
            notif.next_attempt_at = None
            stats["dead_letter"] += 1
            return
        delay = backoff_delay(
            notif.attempts,
            base_seconds=self.settings.notify_retry_base_seconds,
            max_seconds=self.settings.notify_retry_max_seconds,
            rng=self._rng,
        )
        notif.status = NotificationStatus.FAILED
        notif.next_attempt_at = self._clock() + timedelta(seconds=delay)
        stats["failed"] += 1

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        """ISO-8601 with the zone offset the column carries (UTC in practice)."""
        return value.isoformat() if value is not None else None

    def _render(self, alert: Alert, transition: AlertTransition) -> dict:
        family = alert_family(alert.source_type)
        return {
            "title": alert.title,
            "body": alert.description or alert.title,
            "severity": Severity(alert.severity).value,
            "transition": transition.value,
            "alert_id": str(alert.id),
            "fingerprint": alert.fingerprint,
            # MAIL-1: what an e-mail template needs beyond the shared fields.
            "alert_type": alert.source_type,
            "opened_at": self._iso(alert.opened_at),
            "instance": (alert.context or {}).get("instance"),
            "fields": {
                "occurrences": alert.occurrence_count,
                "first_seen": self._iso(alert.first_seen_at),
                **{k: v for k, v in (alert.evidence_snapshot or {}).items()
                   if k in ("observed_value", "expected_value", "deviation_score", "reason")},
            },
            # SOC-12a: the inputs the versioned generic-webhook contract (v1) needs.
            # Additive only -- every key above keeps its name and value, so the
            # channels that read this payload today send exactly what they sent
            # before. `family` reuses the alert-list mapping (and is null for a
            # producer that mapping does not know).
            "family": family.value if family is not None else None,
            # The source id is frozen as a URN: `redact` masks a bare UUID as
            # token-shaped (it does that to `alert_id` too), and unlike `alert_id`
            # this one is needed at delivery time, when only the notification row
            # is loaded. The v1 builder emits the bare UUID from this form.
            "source_id": f"urn:uuid:{alert.source_id}" if alert.source_id else None,
            "status": AlertStatus(alert.status).value,
            "acknowledged_at": self._iso(alert.acknowledged_at),
            # A principal is frozen with a scheme for the same reason: a username of
            # 24+ token-shaped characters would otherwise leave as ***redacted***.
            "acknowledged_by": (
                f"principal:{alert.acknowledged_by}" if alert.acknowledged_by else None
            ),
            "resolved_at": self._iso(alert.resolved_at),
            "resolved_by": f"principal:{alert.resolved_by}" if alert.resolved_by else None,
            # The code only: a resolution's free text is never part of a payload.
            "resolution_reason_code": alert.resolution_reason_code,
        }

    def _message_from_payload(self, notif: AlertNotification) -> NotificationMessage:
        p = notif.payload or {}
        return NotificationMessage(
            title=p.get("title", "alert"),
            body=p.get("body", ""),
            severity=Severity(p.get("severity", "MEDIUM")),
            # These columns are String-backed, so a row loaded from the database
            # yields a plain str rather than the annotated enum. StrEnum accepts
            # both, so construct from the raw value instead of reaching for .value.
            transition=AlertTransition(p.get("transition") or notif.transition),
            # The column, not the payload: ``redact`` masks a UUID as token-shaped, so the
            # stored copy reads ***redacted*** and a link built from it would be dead.
            alert_id=str(notif.alert_id),
            fingerprint=p.get("fingerprint", ""),
            fields=p.get("fields", {}),
            alert_type=p.get("alert_type"),
            opened_at=p.get("opened_at"),
            instance=p.get("instance"),
        )
