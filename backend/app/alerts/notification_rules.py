"""Notification routing rules: the pure matcher and the bulk loader (SOC-12c).

Rules **add** named destinations to an alert's routes (D-72). Today's environment
routes and stored e-mail recipients stay unless a matching rule sets ``stop_default``.
The matcher touches no database and sends nothing; SOC-12d wires the dispatcher.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.query import alert_family
from app.models.enums import SEVERITY_RANK
from app.models.notification_destination import NotificationDestination
from app.models.notification_rule import NotificationRule


class AlertView(Protocol):
    """The alert fields the matcher reads (the ``Alert`` ORM row satisfies it)."""

    severity: str
    source_type: str
    context: dict | None


@dataclass(frozen=True)
class RuleView:
    """One enabled rule, with ``destination_ids`` already limited to enabled destinations."""

    id: uuid.UUID
    priority: int
    min_severity: str
    destination_ids: tuple[uuid.UUID, ...]
    stop_default: bool = False
    enabled: bool = True
    families: frozenset[str] | None = None
    owners: frozenset[str] | None = None
    source_classes: frozenset[str] | None = None
    group_ids: frozenset[int] | None = None
    window_start_minute: int | None = None
    window_end_minute: int | None = None


@dataclass(frozen=True)
class RuleMatch:
    destination_ids: tuple[uuid.UUID, ...]
    stop_default: bool


def _in_window(rule: RuleView, at: datetime) -> bool:
    start, end = rule.window_start_minute, rule.window_end_minute
    if start is None or end is None:
        return True
    utc = at.astimezone(UTC)
    minute = utc.hour * 60 + utc.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # start > end wraps midnight


def _group_ids(context: dict) -> set[int]:
    raw = context.get("group_ids")
    if not isinstance(raw, list):
        return set()
    return {g for g in raw if isinstance(g, int) and not isinstance(g, bool)}


def _matches(rule: RuleView, alert: AlertView, at: datetime) -> bool:
    if not rule.enabled:
        return False
    # An unknown stored severity ranks 0 and never matches (fail closed).
    if SEVERITY_RANK.get(alert.severity, 0) < SEVERITY_RANK.get(rule.min_severity, 6):
        return False
    context = alert.context or {}
    if rule.families is not None:
        family = alert_family(alert.source_type)
        if family is None or family.value not in rule.families:
            return False
    if rule.owners is not None and context.get("owner") not in rule.owners:
        return False
    if rule.source_classes is not None and context.get("source_class") not in rule.source_classes:
        return False
    if rule.group_ids is not None and not (rule.group_ids & _group_ids(context)):
        return False
    return _in_window(rule, at)


def match_rules(rules: Iterable[RuleView], alert: AlertView, *, at: datetime) -> RuleMatch:
    """Destinations for one alert at transition time ``at``: priority, then id, de-duplicated."""
    ordered = sorted(rules, key=lambda r: (r.priority, str(r.id)))
    destinations: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    stop = False
    for rule in ordered:
        if not _matches(rule, alert, at):
            continue
        stop = stop or rule.stop_default
        for destination_id in rule.destination_ids:
            if destination_id not in seen:
                seen.add(destination_id)
                destinations.append(destination_id)
    return RuleMatch(tuple(destinations), stop)


def _frozen(values: Sequence[object] | None) -> frozenset | None:
    return None if values is None else frozenset(values)


async def load_rules(session: AsyncSession) -> list[RuleView]:
    """All enabled rules, limited to enabled destinations, in at most two queries.

    A rule left with no enabled destination is dropped: it could deliver nothing, so it
    must not suppress the default routes through ``stop_default`` either.
    """
    rows = list(
        (await session.scalars(select(NotificationRule).where(NotificationRule.enabled))).all()
    )
    if not rows:
        return []
    wanted = {d for row in rows for d in row.destination_ids}
    live = set(
        (
            await session.scalars(
                select(NotificationDestination.id).where(
                    NotificationDestination.enabled, NotificationDestination.id.in_(wanted)
                )
            )
        ).all()
    )
    views: list[RuleView] = []
    for row in rows:
        destinations = tuple(d for d in row.destination_ids if d in live)
        if not destinations:
            continue
        views.append(
            RuleView(
                id=row.id,
                priority=row.priority,
                min_severity=row.min_severity,
                destination_ids=destinations,
                stop_default=row.stop_default,
                families=_frozen(row.families),
                owners=_frozen(row.owners),
                source_classes=_frozen(row.source_classes),
                group_ids=_frozen(row.group_ids),
                window_start_minute=row.window_start_minute,
                window_end_minute=row.window_end_minute,
            )
        )
    return views
