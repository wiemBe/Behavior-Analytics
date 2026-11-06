"""Pure resolution of the SOC-17c alert policy, and its per-run cache.

The engines and the daily digest all need one answer for "for this source (or
class) and this detector, does the anomaly page, get recorded only, or go to the
digest?". That answer is a pure function of the policy rows, so it lives here.

Precedence, highest first (design §4.3):

  1. a ``SOURCE`` row for the source and this detector;
  2. a ``SOURCE`` row for the source with ``detector_type`` NULL;
  3. a ``CLASS`` row for the source's effective class and this detector;
  4. a ``CLASS`` row for the class with ``detector_type`` NULL;
  5. the configured default, ``ALERT``.

Within one of those steps an instance-specific row beats a global
(``instance_id`` NULL) row, the same rule as ``app.fleet.outage_policy._pick``.
Property watch has no class: its SOURCE scope is the watch id and steps 3 and 4
are skipped. For WAF and IPS the SOURCE scope is the **log source**, not the
endpoint or entity.

The gate below loads the table once per engine run and memoises the verdict per
``(instance, family, source, class, detector)``, so a run costs one statement
however many sources and buckets it evaluates (AGENTS.md rule 9).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_policy import AlertPolicy
from app.models.enums import AlertPolicyMode, AlertPolicyScope, AnomalyFamily


class AlertPolicyRow(Protocol):
    """The shape :func:`resolve` reads.

    Duck-typed so the ORM row and a test double both qualify without importing
    the model.
    """

    instance_id: uuid.UUID | None
    scope_kind: AlertPolicyScope
    scope_value: str
    family: AnomalyFamily
    detector_type: str | None
    mode: AlertPolicyMode


@dataclass(frozen=True)
class ResolvedAlertPolicy:
    mode: AlertPolicyMode
    #: SOURCE_DETECTOR, SOURCE, CLASS_DETECTOR, CLASS or DEFAULT -- which step won.
    basis: str


def _pick(
    rows: list[AlertPolicyRow],
    *,
    scope: AlertPolicyScope,
    value: str,
    family: AnomalyFamily,
    detector: str | None,
    instance_id: uuid.UUID | None,
) -> AlertPolicyRow | None:
    """The winning row for one scope, detector level and family, or None.

    An instance-specific row for the caller's instance beats a global one; a row
    for another instance never applies. With no instance in hand only global rows
    can apply.
    """
    candidates = [
        row
        for row in rows
        if row.scope_kind == scope.value
        and row.scope_value == value
        and row.family == family.value
        and row.detector_type == detector
    ]
    if not candidates:
        return None
    if instance_id is not None:
        for row in candidates:
            if row.instance_id == instance_id:
                return row
    return next((row for row in candidates if row.instance_id is None), None)


def resolve(
    policy_rows: Iterable[AlertPolicyRow],
    *,
    instance_id: uuid.UUID | None,
    family: AnomalyFamily,
    source_id: uuid.UUID | None,
    source_class: str | None,
    detector: str | None,
    default: AlertPolicyMode,
) -> ResolvedAlertPolicy:
    """Resolve the alert mode for one source, class and detector.

    ``source_id`` is None for a class-only resolution. ``source_class`` is
    ignored for property watch, which has no class scope. ``detector`` None never
    matches a detector-specific row; callers pass the incident's detector value.
    """
    rows = list(policy_rows)
    if source_id is not None:
        row = _pick(
            rows,
            scope=AlertPolicyScope.SOURCE,
            value=str(source_id),
            family=family,
            detector=detector,
            instance_id=instance_id,
        )
        if row is not None:
            return ResolvedAlertPolicy(AlertPolicyMode(row.mode), "SOURCE_DETECTOR")
        row = _pick(
            rows,
            scope=AlertPolicyScope.SOURCE,
            value=str(source_id),
            family=family,
            detector=None,
            instance_id=instance_id,
        )
        if row is not None:
            return ResolvedAlertPolicy(AlertPolicyMode(row.mode), "SOURCE")
    if source_class is not None and family is not AnomalyFamily.PROPERTY_WATCH:
        row = _pick(
            rows,
            scope=AlertPolicyScope.CLASS,
            value=str(source_class),
            family=family,
            detector=detector,
            instance_id=instance_id,
        )
        if row is not None:
            return ResolvedAlertPolicy(AlertPolicyMode(row.mode), "CLASS_DETECTOR")
        row = _pick(
            rows,
            scope=AlertPolicyScope.CLASS,
            value=str(source_class),
            family=family,
            detector=None,
            instance_id=instance_id,
        )
        if row is not None:
            return ResolvedAlertPolicy(AlertPolicyMode(row.mode), "CLASS")
    return ResolvedAlertPolicy(default, "DEFAULT")


class AlertPolicyGate:
    """The policy table loaded once per engine run, with a memoised verdict.

    Construct one per engine instance (or per digest run), not per bucket.
    """

    def __init__(
        self, session: AsyncSession, *, default: AlertPolicyMode
    ) -> None:
        self._session = session
        self._default = default
        self._rows: list[AlertPolicy] | None = None
        self._cache: dict[
            tuple[str, str, str, str | None, str | None], ResolvedAlertPolicy
        ] = {}

    async def _load(self) -> list[AlertPolicy]:
        if self._rows is None:
            self._rows = list(
                (await self._session.scalars(select(AlertPolicy))).all()
            )
        return self._rows

    async def has_class_rows(self) -> bool:
        """Whether any CLASS row exists. A False answer is why a run needs no class map."""
        return any(
            row.scope_kind == AlertPolicyScope.CLASS.value for row in await self._load()
        )

    async def resolved(
        self,
        *,
        instance_id: uuid.UUID | None,
        family: AnomalyFamily,
        source_id: uuid.UUID | None,
        source_class: str | None,
        detector: str | None,
    ) -> ResolvedAlertPolicy:
        key = (
            str(instance_id),
            family.value,
            str(source_id),
            str(source_class) if source_class is not None else None,
            detector,
        )
        cached = self._cache.get(key)
        if cached is None:
            cached = resolve(
                await self._load(),
                instance_id=instance_id,
                family=family,
                source_id=source_id,
                source_class=source_class,
                detector=detector,
                default=self._default,
            )
            self._cache[key] = cached
        return cached

    async def mode(
        self,
        *,
        instance_id: uuid.UUID | None,
        family: AnomalyFamily,
        source_id: uuid.UUID | None,
        source_class: str | None,
        detector: str | None,
    ) -> AlertPolicyMode:
        verdict = await self.resolved(
            instance_id=instance_id,
            family=family,
            source_id=source_id,
            source_class=source_class,
            detector=detector,
        )
        return verdict.mode
