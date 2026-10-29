"""Alert seam for custom threshold rules (design §F.6, D-17).

The fold emits ``open``, ``refresh`` and ``resolve`` events. This module turns
them into one ``AlertService.apply_bulk`` call per tick, then enqueues a
notification per transition the way ``SearchAlertEvaluator`` does. A refresh
has no transition, so it bumps ``occurrence_count`` and does not page again.

Shadow mode (``custom_rules_alerting_enabled`` false, the default) still
records breaches. ``compile_batch`` returns None, the engine keeps the no-op
sink, and nothing is opened, refreshed, resolved or enqueued.

F-3 exposes the mode on each rule as ``alerting_mode`` (``shadow`` or
``alerting``). It is not a column and not a ``last_status_reason``: that
sentence is the evaluation honesty (``STALE``, maintenance, ``DISABLED``).
The mode is the platform setting, the same value for every rule. Call
``alerting_mode``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.fingerprint import custom_rule_breach_fingerprint
from app.alerts.service import AlertInput, AlertResolution, AlertResult, AlertService
from app.i18n.messages import MessageRef, render
from app.models.alert import Alert
from app.models.enums import AlertStatus, Severity

#: What ``GET /custom-rules`` should call the field. Not stored on the rule row.
ALERTING_MODE_FIELD = "alerting_mode"
ALERTING_MODE_SHADOW = "shadow"
ALERTING_MODE_ALERTING = "alerting"

SOURCE_TYPE = "custom_rule"
EVALUATOR_ACTOR = "custom-rule-evaluator"
REASON_RECOVERED = "condition recovered"
REASON_DISABLED = "the rule is disabled"
REASON_RETIRED = "the subject is retired"
REASON_DELETED = "the rule was deleted"
MESSAGE_RECOVERED = MessageRef("alert.custom_rule.recovered")
MESSAGE_DISABLED = MessageRef("custom_rule.status.disabled")
MESSAGE_RETIRED = MessageRef("custom_rule.status.subject_retired")
MESSAGE_DELETED = MessageRef("alert.custom_rule.deleted")

_STATUS_DISABLED = "DISABLED"
_STATUS_RETIRED = "SUBJECT_RETIRED"


def alerting_mode(alerting_enabled: bool) -> str:
    """``alerting_mode`` for F-3. ``shadow`` pages nothing; ``alerting`` does."""
    if alerting_enabled:
        return ALERTING_MODE_ALERTING
    return ALERTING_MODE_SHADOW


def extra_resolve_reason(
    status: str | None,
    status_reason: str | None,
    *,
    breach_open: bool,
    alerting_enabled: bool,
) -> str | None:
    """Why an open alert should close when the fold emitted no resolve.

    None means keep the alert. Shadow mode always returns None: a disabled
    rule, a retired subject and a recovery that happened while paging was off
    wait until alerting is on. A live breach is not closed for ``STALE``,
    ``NOT_COLLECTED`` or maintenance — those are not unders (§F.5, §F.6).
    A disabled rule and a retired subject cannot recover, so their alert
    closes even though the breach row stays. A rule with no live breach
    closes a page that recovery already earned, including one earned in
    shadow mode.
    """
    if not alerting_enabled:
        return None
    if status == _STATUS_DISABLED:
        return REASON_DISABLED
    if status == _STATUS_RETIRED:
        return status_reason or REASON_RETIRED
    if breach_open:
        return None
    return REASON_RECOVERED


def extra_resolve_message(
    status: str | None,
    status_reason_message: MessageRef | None,
    *,
    breach_open: bool,
    alerting_enabled: bool,
) -> MessageRef | None:
    """Coded counterpart of :func:`extra_resolve_reason` for stored alert metadata."""
    if not alerting_enabled:
        return None
    if status == _STATUS_DISABLED:
        return MESSAGE_DISABLED
    if status == _STATUS_RETIRED:
        return status_reason_message or MESSAGE_RETIRED
    if breach_open:
        return None
    return MESSAGE_RECOVERED


@dataclass(frozen=True)
class CustomRuleAlertEvent:
    """One breach transition the sink should reflect as an alert.

    Counts, shares, labels and bucket bounds only (AGENTS.md rule 3). `action`
    is ``open`` when the breach starts, ``refresh`` while it is still breaching,
    and ``resolve`` after recovery.
    """

    action: str
    rule_id: uuid.UUID
    breach_id: uuid.UUID
    rule_name: str
    severity: str
    subject_kind: str
    subject_label: str
    metric: str
    unit: str
    operator: str
    threshold_value: float
    observed_value: float | None
    measurement: str | None
    first_bucket_start: datetime
    last_bucket_start: datetime
    unmeasured_buckets: int
    reason: str | None = None
    reason_message: MessageRef | None = None


class CustomRuleAlertSink(Protocol):
    """What the engine calls inside its transaction. ``flush`` is optional.

    The no-op sink has no ``flush``. The alerting sink buffers, then ``flush``
    writes every rule in the tick in one bulk call.
    """

    async def open_or_update(self, event: CustomRuleAlertEvent) -> None:
        """Open the breach's alert, or refresh it while the condition still holds."""

    async def resolve(self, event: CustomRuleAlertEvent) -> None:
        """Resolve the breach's alert. `event.reason` says why."""


class NoOpCustomRuleAlertSink:
    """Shadow mode: the breach row is the whole record, and nothing is paged."""

    async def open_or_update(self, event: CustomRuleAlertEvent) -> None:
        return None

    async def resolve(self, event: CustomRuleAlertEvent) -> None:
        return None


@dataclass(frozen=True)
class AlertBatch:
    """One tick of alert work. Empty means the tick does not touch alert rows."""

    opens: tuple[AlertInput, ...]
    resolves: tuple[AlertResolution, ...]


class _BulkAlerts(Protocol):
    async def apply_bulk(
        self,
        open_or_updates: Sequence[AlertInput],
        resolutions: Sequence[AlertResolution],
    ) -> tuple[list[AlertResult], list[AlertResult | None]]:
        """Open, refresh and resolve a tick in one active-alert lookup."""


def compile_batch(
    events: Sequence[CustomRuleAlertEvent],
    *,
    alerting_enabled: bool,
) -> AlertBatch | None:
    """The alert rows this tick would write. None while shadow mode is on.

    Returning None, rather than an empty batch, is the switch: the caller
    must not open a lookup. Opens stay in order so a later refresh in the
    same tick bumps the alert this tick just opened. The first resolve for a
    rule wins; a second one would page twice.
    """
    if not alerting_enabled:
        return None
    opens: list[AlertInput] = []
    resolves: list[AlertResolution] = []
    seen: set[uuid.UUID] = set()
    for event in events:
        if event.action == "resolve":
            if event.rule_id in seen:
                continue
            seen.add(event.rule_id)
            resolves.append(
                AlertResolution(
                    fingerprint=custom_rule_breach_fingerprint(event.rule_id),
                    actor=EVALUATOR_ACTOR,
                    reason=event.reason or REASON_RECOVERED,
                    reason_message=(
                        event.reason_message
                        if event.reason is not None
                        else MESSAGE_RECOVERED
                    ),
                )
            )
            continue
        if event.action not in ("open", "refresh"):
            continue
        opens.append(_alert_input(event))
    return AlertBatch(opens=tuple(opens), resolves=tuple(resolves))


def _alert_input(event: CustomRuleAlertEvent) -> AlertInput:
    metric = f"{event.metric} ({event.unit})" if event.unit else event.metric
    title_message = MessageRef("alert.threshold.crossed", {"name": event.rule_name})
    description_message = _description_message(event)
    return AlertInput(
        fingerprint=custom_rule_breach_fingerprint(event.rule_id),
        title=render(title_message),
        title_message=title_message,
        severity=Severity(event.severity),
        source_type=SOURCE_TYPE,
        source_id=event.rule_id,
        description=render(description_message),
        description_message=description_message,
        evidence={
            "observed_value": event.observed_value,
            "measurement": event.measurement,
            "threshold": event.threshold_value,
            "operator": event.operator,
            "first_bucket_start": event.first_bucket_start.isoformat(),
            "last_bucket_start": event.last_bucket_start.isoformat(),
            "unmeasured_buckets": event.unmeasured_buckets,
        },
        context={
            "rule": event.rule_name,
            "subject_kind": event.subject_kind,
            "subject_label": event.subject_label,
            "metric": metric,
        },
    )


def _description(event: CustomRuleAlertEvent) -> str:
    return render(_description_message(event))


def _description_message(event: CustomRuleAlertEvent) -> MessageRef:
    measured = "unmeasured" if event.observed_value is None else (
        "lower_bound" if event.measurement == "LOWER_BOUND" else "exact"
    )
    suffix = "_unit" if event.unit else ""
    params: dict[str, object] = {
        "name": event.rule_name,
        "operator": event.operator,
        "threshold": event.threshold_value,
    }
    if event.observed_value is not None:
        params["observed"] = event.observed_value
    if event.unit:
        params["unit"] = event.unit
    return MessageRef(f"alert.custom_rule.description.{measured}{suffix}", params)


def _resolve_only_event(
    rule_id: uuid.UUID,
    reason: str,
    now: datetime,
    reason_message: MessageRef | None = None,
) -> CustomRuleAlertEvent:
    """A resolve the fold did not emit. Only the id, action and reason are read."""
    return CustomRuleAlertEvent(
        action="resolve",
        rule_id=rule_id,
        breach_id=rule_id,
        rule_name="",
        severity=Severity.INFO.value,
        subject_kind="",
        subject_label="",
        metric="",
        unit="",
        operator="",
        threshold_value=0.0,
        observed_value=None,
        measurement=None,
        first_bucket_start=now,
        last_bucket_start=now,
        unmeasured_buckets=0,
        reason=reason,
        reason_message=reason_message,
    )


class AlertingCustomRuleSink:
    """Buffer a tick, then write it through ``AlertService.apply_bulk``.

    One lookup covers every rule. A per-rule ``open_or_update`` would make
    the statement count grow with the rule count, which §F.4.2 forbids.
    ``watch`` is the set of rules that still exist; an active custom-rule
    alert whose rule is not in that set is a deleted rule.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        enqueuer: AlertEnqueuer | None = None,
        clock: Callable[[], datetime] | None = None,
        alerts: _BulkAlerts | None = None,
        scan_deleted: bool = True,
    ) -> None:
        self.session = session
        self._enqueuer = enqueuer
        self._clock = clock or _utc_now
        self.alerts: _BulkAlerts = alerts if alerts is not None else AlertService(
            session, clock=self._clock
        )
        self._scan_deleted = scan_deleted
        self._events: list[CustomRuleAlertEvent] = []
        self._known: set[uuid.UUID] = set()

    def watch(self, rule_id: uuid.UUID) -> None:
        """The rule still exists. Its alert is not an orphan."""
        self._known.add(rule_id)

    async def open_or_update(self, event: CustomRuleAlertEvent) -> None:
        self._events.append(event)
        self._known.add(event.rule_id)

    async def resolve(self, event: CustomRuleAlertEvent) -> None:
        self._events.append(event)
        self._known.add(event.rule_id)

    async def flush(self) -> None:
        """Write the buffered tick. A sink error propagates and the tick rolls back.

        The buffer is dropped once it has been compiled, so a second flush
        cannot page the same transitions again.
        """
        if self._scan_deleted:
            await self._queue_deleted()
        batch = compile_batch(self._events, alerting_enabled=True)
        self._events.clear()
        if batch is None or (not batch.opens and not batch.resolves):
            return
        opened, resolved = await self.alerts.apply_bulk(batch.opens, batch.resolves)
        if self._enqueuer is None:
            return
        for result in (*opened, *resolved):
            # None transition is a refresh: the dedup guarantee. Do not page it.
            if result is not None and result.transition is not None:
                await self._enqueuer.enqueue(result.alert, result.transition)

    async def _queue_deleted(self) -> None:
        """Resolve pages whose rule row is already gone (a cascade, or F-3).

        F-3 should call ``resolve_deleted_rule_alert`` first, with the deleting
        principal. This sweep is the backstop when that call did not happen.
        The actor is the evaluator, and the reason says the rule was deleted.
        """
        statement = select(Alert.source_id).where(
            Alert.source_type == SOURCE_TYPE,
            Alert.status != AlertStatus.RESOLVED,
            Alert.source_id.is_not(None),
        )
        if self._known:
            statement = statement.where(Alert.source_id.notin_(list(self._known)))
        found = (await self.session.scalars(statement)).all()
        already = {event.rule_id for event in self._events if event.action == "resolve"}
        now = self._clock()
        for rule_id in found:
            if rule_id is None or rule_id in already:
                continue
            self._events.append(
                _resolve_only_event(rule_id, REASON_DELETED, now, MESSAGE_DELETED)
            )
            already.add(rule_id)


async def resolve_deleted_rule_alert(
    session: AsyncSession,
    rule_id: uuid.UUID,
    *,
    actor: str,
    enqueuer: AlertEnqueuer | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AlertResult | None:
    """Resolve the rule's open alert before the rule row is deleted.

    Design §F.4.3: the actor is the deleting principal, not the evaluator.
    Reason is ``the rule was deleted``. No open alert is a no-op, and it
    does not enqueue. F-3 calls this, then deletes.
    """
    if not actor.strip():
        raise ValueError("actor is required")
    service = AlertService(session, clock=clock)
    result = await service.resolve_by_fingerprint(
        custom_rule_breach_fingerprint(rule_id),
        actor=actor,
        reason=REASON_DELETED,
        reason_message=MESSAGE_DELETED,
    )
    if enqueuer is not None and result is not None and result.transition is not None:
        await enqueuer.enqueue(result.alert, result.transition)
    return result


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ALERTING_MODE_ALERTING",
    "ALERTING_MODE_FIELD",
    "ALERTING_MODE_SHADOW",
    "EVALUATOR_ACTOR",
    "REASON_DELETED",
    "REASON_DISABLED",
    "REASON_RECOVERED",
    "REASON_RETIRED",
    "SOURCE_TYPE",
    "AlertBatch",
    "AlertingCustomRuleSink",
    "CustomRuleAlertEvent",
    "CustomRuleAlertSink",
    "NoOpCustomRuleAlertSink",
    "alerting_mode",
    "compile_batch",
    "extra_resolve_message",
    "extra_resolve_reason",
    "resolve_deleted_rule_alert",
]
