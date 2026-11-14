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
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.notification_rules import AlertView, RuleView, load_rules, match_rules
from app.alerts.notifiers import destination_sender
from app.alerts.notifiers.base import NotificationMessage, Notifier, NotifierError
from app.alerts.notifiers.console_hosts import registered_console_hosts, targets_console
from app.alerts.notifiers.destination_sender import (
    DESTINATION_TARGET_PREFIX,
    StoredHeadersInvalid,
    destination_target_id,
)
from app.alerts.notifiers.url_guard import resolve_host
from app.alerts.notifiers.webhook_v1 import build_webhook_v1
from app.alerts.query import alert_family
from app.alerts.routing import Route, RoutingPolicy, RoutingRule
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
from app.models.notification_attempt import MAX_ATTEMPTS_KEPT, NotificationAttempt
from app.models.notification_destination import NotificationDestination
from app.security.redaction import redact
from app.services.backoff import backoff_delay

# Failure classes from the sender that retry; every other non-ok class is permanent.
#: Channels whose target is a URL the product POSTs to.
_URL_CHANNELS = frozenset(
    {NotificationChannel.TEAMS, NotificationChannel.SLACK, NotificationChannel.WEBHOOK}
)
_PERMANENT_STATUS_CLASSES = frozenset({"redirect_refused", "ssrf_refused"})


_STATUS_IN_MESSAGE = re.compile(r"status (\d{3})$")


def _classify_notifier_error(exc: NotifierError) -> tuple[str, int | None]:
    """The stored attempt class of an env-channel failure; the message itself is not kept."""
    match = _STATUS_IN_MESSAGE.search(str(exc))
    if match is not None:
        return "http_status", int(match.group(1))
    return ("permanent" if exc.permanent else "transport"), None


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
        destination_resolver: destination_sender.Resolver = resolve_host,
        destination_transport: destination_sender.TransportFactory | None = None,
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
        # SOC-12d. Notification rules (with their enabled destinations) load once.
        self._rule_views: list[RuleView] | None = None
        # SOC-12e. Attempts of this run, written in bulk at its end.
        self._attempt_log: list[dict[str, object]] = []
        self._console_hosts: frozenset[str] = frozenset()
        self._destination_resolver = destination_resolver
        self._destination_transport = destination_transport or destination_sender.http_transport

    async def _rules(self) -> list[RuleView]:
        if self._rule_views is None:
            self._rule_views = await load_rules(self.session)
        return self._rule_views

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

        match = match_rules(
            await self._rules(),
            cast(AlertView, alert),  # the ORM row satisfies the read-only view
            at=self._transition_time(alert, transition),
        )
        # A rule only adds destinations; today's routes stay unless a match says stop.
        routes = [] if match.stop_default else self.policy.resolve(
            alert, await self._stored_email_rules()
        )
        routes = [
            *routes,
            *(
                Route(NotificationChannel.WEBHOOK, f"{DESTINATION_TARGET_PREFIX}{destination}")
                for destination in match.destination_ids
            ),
        ]

        created: list[AlertNotification] = []
        for route in routes:
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

    def _transition_time(self, alert: Alert, transition: AlertTransition) -> datetime:
        """The stored time of the transition, so a rule window judges the event, not the retry."""
        stamp = {
            AlertTransition.OPENED: alert.opened_at,
            AlertTransition.ACKNOWLEDGED: alert.acknowledged_at,
            AlertTransition.RESOLVED: alert.resolved_at,
        }.get(transition)
        return stamp if stamp is not None else self._clock()

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
        # One query for every destination this run delivers to, however many rows.
        wanted = {
            destination
            for n in due
            if n.channel == NotificationChannel.WEBHOOK
            and (destination := destination_target_id(n.target)) is not None
        }
        destinations: dict[uuid.UUID, NotificationDestination] = {}
        # Loaded once per run: a console may be registered after a route or destination was.
        self._console_hosts = (
            await registered_console_hosts(self.session)
            if any(n.channel in _URL_CHANNELS for n in due)
            else frozenset()
        )
        if wanted:
            rows = await self.session.scalars(
                select(NotificationDestination).where(NotificationDestination.id.in_(wanted))
            )
            destinations = {row.id: row for row in rows}
        self._attempt_log = []
        for notif in due:
            if (
                notif.channel == NotificationChannel.WEBHOOK
                and notif.target.startswith(DESTINATION_TARGET_PREFIX)
            ):
                await self._attempt_destination(notif, destinations, stats)
                continue
            await self._attempt(notif, stats)
        await self.session.flush()
        await self._write_attempts()
        return stats

    def _record(
        self, notif: AlertNotification, error_class: str, status: int | None = None
    ) -> None:
        self._attempt_log.append(
            {
                "id": uuid.uuid4(),
                "notification_id": notif.id,
                "attempted_at": self._clock(),
                "http_status": status,
                "error_class": error_class,
            }
        )

    async def _write_attempts(self) -> None:
        """Store this run's attempts and trim each notification to the newest eight.

        Two statements for the whole run, however many notifications were due: one
        multi-row INSERT and one DELETE over a window function (AGENTS 2.9).
        """
        log, self._attempt_log = self._attempt_log, []
        if not log:
            return
        await self.session.execute(insert(NotificationAttempt), log)
        ids = {entry["notification_id"] for entry in log}
        ranked = (
            select(
                NotificationAttempt.id,
                func.row_number()
                .over(
                    partition_by=NotificationAttempt.notification_id,
                    order_by=(
                        NotificationAttempt.attempted_at.desc(),
                        NotificationAttempt.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(NotificationAttempt.notification_id.in_(ids))
            .subquery()
        )
        await self.session.execute(
            delete(NotificationAttempt).where(
                NotificationAttempt.id.in_(
                    select(ranked.c.id).where(ranked.c.rank > MAX_ATTEMPTS_KEPT)
                )
            )
        )

    async def _attempt(self, notif: AlertNotification, stats: dict[str, int]) -> None:
        notifier = self.notifiers.get(notif.channel)
        notif.attempts += 1
        if notifier is None:
            notif.status = NotificationStatus.DEAD_LETTER
            notif.error_message = f"no notifier registered for {notif.channel}"
            stats["dead_letter"] += 1
            self._record(notif, "no_notifier")
            return

        if notif.channel in _URL_CHANNELS and targets_console(notif.target, self._console_hosts):
            # An env route aimed at a registered console: never sent, and a retry cannot help.
            self._record(notif, "ssrf_refused")
            self._handle_failure(
                notif, NotifierError("ssrf_refused: console_refused", permanent=True), stats
            )
            return

        message = self._message_from_payload(notif)
        if notif.channel == NotificationChannel.EMAIL:
            from app.services.email_templates import choose_template

            message.template = choose_template(await self._templates(), message.alert_type)
        try:
            await notifier.send(notif.target, message)
        except NotifierError as exc:
            self._record(notif, *_classify_notifier_error(exc))
            self._handle_failure(notif, exc, stats)
            return
        except Exception as exc:
            wrapped = NotifierError(str(exc))
            self._record(notif, *_classify_notifier_error(wrapped))
            self._handle_failure(notif, wrapped, stats)
            return

        notif.status = NotificationStatus.SENT
        notif.sent_at = self._clock()
        notif.error_message = None
        stats["sent"] += 1
        self._record(notif, "ok")

    async def _attempt_destination(
        self,
        notif: AlertNotification,
        destinations: dict[uuid.UUID, NotificationDestination],
        stats: dict[str, int],
    ) -> None:
        """Deliver one row to its named destination: the v1 body through the pinned sender."""
        notif.attempts += 1
        destination_id = destination_target_id(notif.target)
        row = destinations.get(destination_id) if destination_id is not None else None
        if row is None or not row.enabled:
            self._record(notif, "no_destination")
            self._handle_failure(notif, NotifierError("no_destination", permanent=True), stats)
            return
        try:
            body, headers = build_webhook_v1(
                notif.payload or {},
                alert_id=notif.alert_id,
                transition=notif.transition,
                destination_id=str(row.id),
                public_origin=self.settings.public_origin,
            )
        except ValueError:
            # A stored payload that cannot meet the contract will not improve on retry.
            self._record(notif, "payload_invalid")
            self._handle_failure(notif, NotifierError("payload_invalid", permanent=True), stats)
            return
        try:
            result, http_status = await destination_sender.send_to_destination(
                row,
                body,
                headers,
                console_hosts=self._console_hosts,
                resolver=self._destination_resolver,
                transport_factory=self._destination_transport,
            )
        except StoredHeadersInvalid:
            # A stored header block that fails the schema will not improve on retry.
            self._record(notif, "payload_invalid")
            self._handle_failure(notif, NotifierError("payload_invalid", permanent=True), stats)
            return
        except Exception as exc:
            # The text could carry the URL, so only a class is kept. A protocol
            # error is not an httpx.TransportError; it is still a transport failure.
            failure = destination_sender.transport_failure_class(exc)
            error_class = failure if failure is not None else "transport"
            self._record(notif, error_class)
            self._handle_failure(notif, NotifierError(error_class), stats)
            return
        self._record(notif, result, http_status)
        if result == "ok":
            notif.status = NotificationStatus.SENT
            notif.sent_at = self._clock()
            notif.error_message = None
            stats["sent"] += 1
            return
        # Class and status only: never the URL, the response body, a header or the secret.
        message = result if http_status is None else f"{result} {http_status}"
        permanent = result in _PERMANENT_STATUS_CLASSES or (
            result == "http_status"
            and http_status is not None
            and http_status != 429
            and http_status < 500
        )
        self._handle_failure(notif, NotifierError(message, permanent=permanent), stats)

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
