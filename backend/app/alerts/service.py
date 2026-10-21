"""Alert lifecycle service.

Lifecycle: OPEN -> ACKNOWLEDGED -> RESOLVED.

Deduplication is two-layered (see app/alerts/fingerprint.py):
  1. Application layer (primary): look up the open alert by fingerprint; if one
     exists, update it (bump occurrence_count, refresh evidence, enqueue no
     duplicate notification) instead of opening a new alert.
  2. Database (final safeguard): the partial unique index on
     alert(dedup_key) WHERE status <> 'RESOLVED' catches the concurrent-worker
     race; the service handles the IntegrityError by reloading the winner.

A notification is enqueued exactly once per (alert, transition, channel) — the
notification router owns delivery; this service only records the intent.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, any_, bindparam, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import AlertStatus, AlertTransition, Severity


@dataclass
class AlertInput:
    fingerprint: str
    title: str
    severity: Severity
    source_type: str
    source_id: uuid.UUID | None
    description: str | None = None
    evidence: dict | None = None
    source_anomaly_ids: list[str] | None = None
    context: dict | None = None


@dataclass
class AlertResult:
    alert: Alert
    opened: bool          # a new alert was created
    updated: bool         # an existing open alert was refreshed
    transition: AlertTransition | None


@dataclass(frozen=True)
class AlertResolution:
    fingerprint: str
    actor: str
    reason: str | None = None


class AlertService:
    def __init__(
        self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.session = session
        self._clock = clock or (lambda: datetime.now(UTC))

    async def open_or_update(self, data: AlertInput) -> AlertResult:
        """Open a new alert or update the existing open one for this condition."""
        now = self._clock()
        existing = await self._find_active(data.fingerprint)

        if existing is not None:
            existing.occurrence_count += 1
            existing.last_seen_at = now
            existing.severity = data.severity
            if data.evidence is not None:
                existing.evidence_snapshot = data.evidence
            if data.source_anomaly_ids:
                merged = set(existing.source_anomaly_ids) | set(data.source_anomaly_ids)
                existing.source_anomaly_ids = sorted(merged)
            await self.session.flush()
            # No transition -> no notification. This is the dedup guarantee.
            return AlertResult(alert=existing, opened=False, updated=True, transition=None)

        alert = Alert(
            dedup_key=data.fingerprint,
            fingerprint=data.fingerprint,
            title=data.title,
            description=data.description,
            severity=data.severity,
            status=AlertStatus.OPEN,
            source_type=data.source_type,
            source_id=data.source_id,
            context=data.context or {},
            opened_at=now,
            first_seen_at=now,
            last_seen_at=now,
            occurrence_count=1,
            evidence_snapshot=data.evidence or {},
            source_anomaly_ids=data.source_anomaly_ids or [],
        )
        self.session.add(alert)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent worker won the race; reload and treat as update.
            await self.session.rollback()
            existing = await self._find_active(data.fingerprint)
            if existing is None:
                raise
            existing.occurrence_count += 1
            existing.last_seen_at = self._clock()
            await self.session.flush()
            return AlertResult(alert=existing, opened=False, updated=True, transition=None)

        return AlertResult(alert=alert, opened=True, updated=False,
                           transition=AlertTransition.OPENED)

    async def acknowledge(self, alert_id: uuid.UUID, *, actor: str) -> AlertResult:
        alert = await self.session.get(Alert, alert_id)
        if alert is None:
            raise ValueError("alert not found")
        if alert.status != AlertStatus.OPEN:
            # Idempotent / invalid transition: only OPEN -> ACKNOWLEDGED.
            return AlertResult(alert=alert, opened=False, updated=False, transition=None)
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = self._clock()
        alert.acknowledged_by = actor
        await self.session.flush()
        return AlertResult(alert=alert, opened=False, updated=True,
                           transition=AlertTransition.ACKNOWLEDGED)

    async def resolve(
        self, alert_id: uuid.UUID, *, actor: str, reason: str | None = None
    ) -> AlertResult:
        alert = await self.session.get(Alert, alert_id)
        if alert is None:
            raise ValueError("alert not found")
        if alert.status == AlertStatus.RESOLVED:
            return AlertResult(alert=alert, opened=False, updated=False, transition=None)
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = self._clock()
        alert.resolved_by = actor
        alert.resolution_reason = reason
        await self.session.flush()
        return AlertResult(alert=alert, opened=False, updated=True,
                           transition=AlertTransition.RESOLVED)

    async def resolve_by_fingerprint(
        self, fingerprint: str, *, actor: str, reason: str | None = None
    ) -> AlertResult | None:
        alert = await self._find_active(fingerprint)
        if alert is None:
            return None
        return await self.resolve(alert.id, actor=actor, reason=reason)

    async def apply_bulk(
        self,
        open_or_updates: Sequence[AlertInput],
        resolutions: Sequence[AlertResolution],
    ) -> tuple[list[AlertResult], list[AlertResult | None]]:
        """Apply a bucket's alert work after one active-alert lookup.

        Refreshes of alerts that were already open before the call are
        deliberately flushed together, so a bucket with hundreds of
        already-open conditions issues one batched UPDATE, not one statement
        per endpoint. Each new alert retains the partial unique index's
        concurrency protection by inserting inside its own savepoint.
        """
        fingerprints = {
            data.fingerprint for data in open_or_updates
        } | {item.fingerprint for item in resolutions}
        active: dict[str, Alert] = {}
        if fingerprints:
            rows = await self.session.scalars(
                select(Alert).where(
                    Alert.dedup_key
                    == any_(
                        bindparam(
                            "active_alert_fingerprints",
                            type_=ARRAY(String()),
                        )
                    ),
                    Alert.status != AlertStatus.RESOLVED,
                ),
                {"active_alert_fingerprints": sorted(fingerprints)},
            )
            active = {row.dedup_key: row for row in rows}

        results: dict[int, AlertResult] = {}

        # Pass A refreshes every alert that was open in the bulk snapshot, so
        # their field writes leave as one batched UPDATE.
        for index, data in enumerate(open_or_updates):
            existing = active.get(data.fingerprint)
            if existing is None:
                continue
            now = self._clock()
            existing.occurrence_count += 1
            existing.last_seen_at = now
            existing.severity = data.severity
            if data.evidence is not None:
                existing.evidence_snapshot = data.evidence
            if data.source_anomaly_ids:
                merged = set(existing.source_anomaly_ids) | set(data.source_anomaly_ids)
                existing.source_anomaly_ids = sorted(merged)
            results[index] = AlertResult(
                alert=existing, opened=False, updated=True, transition=None
            )

        await self.session.flush()

        # Pass B opens the new alerts in input order. A second input with the
        # same new fingerprint finds the alert the first one created in
        # `active` and takes the refresh branch below. The pre-flush settles
        # whatever an earlier pass-B item left pending (a refresh of an alert
        # created earlier in this call, or the IntegrityError fallback's
        # refresh): flush() no longer accepts the partial-flush objects
        # argument (deprecated in SQLAlchemy 2.1), so each savepoint's nested
        # flush must find only its own new alert and a duplicate fingerprint
        # rolls back alone.
        for index, data in enumerate(open_or_updates):
            if index in results:
                continue
            now = self._clock()
            existing = active.get(data.fingerprint)
            if existing is not None:
                existing.occurrence_count += 1
                existing.last_seen_at = now
                existing.severity = data.severity
                if data.evidence is not None:
                    existing.evidence_snapshot = data.evidence
                if data.source_anomaly_ids:
                    merged = set(existing.source_anomaly_ids) | set(data.source_anomaly_ids)
                    existing.source_anomaly_ids = sorted(merged)
                results[index] = AlertResult(
                    alert=existing, opened=False, updated=True, transition=None
                )
                continue

            alert = Alert(
                dedup_key=data.fingerprint,
                fingerprint=data.fingerprint,
                title=data.title,
                description=data.description,
                severity=data.severity,
                status=AlertStatus.OPEN,
                source_type=data.source_type,
                source_id=data.source_id,
                context=data.context or {},
                opened_at=now,
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
                evidence_snapshot=data.evidence or {},
                source_anomaly_ids=data.source_anomaly_ids or [],
            )
            await self.session.flush()
            try:
                async with self.session.begin_nested():
                    self.session.add(alert)
                    await self.session.flush()
            except IntegrityError:
                # A concurrent worker won after the bulk snapshot. Reload only
                # this exceptional fingerprint and preserve the outer work.
                existing = await self._find_active(data.fingerprint)
                if existing is None:
                    raise
                existing.occurrence_count += 1
                existing.last_seen_at = self._clock()
                active[data.fingerprint] = existing
                results[index] = AlertResult(
                    alert=existing, opened=False, updated=True, transition=None
                )
                continue
            active[data.fingerprint] = alert
            results[index] = AlertResult(
                alert=alert,
                opened=True,
                updated=False,
                transition=AlertTransition.OPENED,
            )

        opened_or_updated = [results[index] for index in range(len(open_or_updates))]

        resolved: list[AlertResult | None] = []
        for item in resolutions:
            resolving = active.get(item.fingerprint)
            if resolving is None:
                resolved.append(None)
                continue
            resolving.status = AlertStatus.RESOLVED
            resolving.resolved_at = self._clock()
            resolving.resolved_by = item.actor
            resolving.resolution_reason = item.reason
            active.pop(item.fingerprint, None)
            resolved.append(
                AlertResult(
                    alert=resolving,
                    opened=False,
                    updated=True,
                    transition=AlertTransition.RESOLVED,
                )
            )

        await self.session.flush()
        return opened_or_updated, resolved

    async def _find_active(self, fingerprint: str) -> Alert | None:
        return await self.session.scalar(
            select(Alert).where(
                Alert.dedup_key == fingerprint,
                Alert.status != AlertStatus.RESOLVED,
            )
        )
