"""Re-check every enabled identity profile. One probe per interval.

The probe and the evaluator are the ones ``validate`` uses. A finished probe
is stored the same way. A probe that does not finish leaves the status. This
does not claim the collection cadence slot: ``validate`` does not either, and
claiming it would steal a collection minute. Ariel spend is the probe's own
permit on the REST provider.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.identity.dto import IdentityProbeResult
from app.identity.profiles import AUDIT_OBJECT, IdentityProfileService
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.base import ProviderCapability, QRadarProvider
from app.repositories.audit import record_audit
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.identity.revalidation")

REVALIDATION_ACTOR = "identity-revalidation"
LOCK_NAME = "identity_revalidation"
_TOKEN_LIMIT = 23
_FAILURE_TOKENS = {
    "ArielPermitUnavailable": "permit_refused",
    "ProviderUnavailableError": "provider_unavailable",
    "TimeoutError": "timeout",
    "ProviderError": "provider_error",
    "ProviderAuthError": "provider_auth",
    "ProviderQueryRejectedError": "query_rejected",
    "CapabilityNotSupportedError": "unsupported",
    "IdentityProfileError": "profile_error",
    "IdentityReferenceError": "reference_error",
}


def failure_token(value: str) -> str:
    """A short reason. A message, a username, or a long class name becomes ``probe_failed``."""
    mapped = _FAILURE_TOKENS.get(value)
    if mapped is not None:
        return mapped
    if value.isidentifier() and len(value) <= _TOKEN_LIMIT:
        return value
    return "probe_failed"


def transport_reason(probe: IdentityProbeResult) -> str | None:
    """A probe that did not finish. A rejected field is mapping drift, not transport."""
    if probe.event_id.rejected or probe.username.rejected:
        return None
    if probe.error:
        return failure_token(probe.error)
    for item in (probe.event_id, probe.username):
        if item.error:
            return failure_token(item.error)
    return None


def revalidation_slot(moment: datetime, interval: int) -> int:
    """The epoch slot of ``moment``. A second call in this slot must not probe again."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("revalidation time must be timezone-aware")
    if interval <= 0:
        raise ValueError("revalidation interval must be positive")
    return int(moment.timestamp()) // interval


@dataclass
class IdentityRevalidationReport:
    status: str = "ok"
    probed: int = 0
    searches: int = 0
    changed: int = 0
    failed: int = 0
    skipped: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "probed": self.probed,
            "searches": self.searches,
            "changed": self.changed,
            "failed": self.failed,
            "skipped": self.skipped,
            "sources": list(self.sources),
        }


class IdentityRevalidator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(
        self, instance: QRadarInstance, provider: QRadarProvider
    ) -> IdentityRevalidationReport:
        report = IdentityRevalidationReport()
        if not provider.supports(ProviderCapability.AQL_EXECUTION):
            report.status = "unsupported"
            logger.info("identity revalidation unsupported: the provider has no ariel search")
            return report
        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, LOCK_NAME
        ) as acquired:
            if not acquired:
                report.status = "skipped_locked"
                report.skipped = 1
                logger.info("identity revalidation skipped: lock held")
                return report
            await self._probe_enabled(instance, provider, report)
        logger.info(
            "identity revalidation %s: %d probes, %d searches, %d changed, %d failed",
            report.status,
            report.probed,
            report.searches,
            report.changed,
            report.failed,
        )
        return report

    async def _probe_enabled(
        self,
        instance: QRadarInstance,
        provider: QRadarProvider,
        report: IdentityRevalidationReport,
    ) -> None:
        now = self._clock()
        slot = revalidation_slot(now, self.settings.identity_revalidation_interval_seconds)
        attempted = now.astimezone(UTC).isoformat()
        service = IdentityProfileService(self.session, settings=self.settings, clock=self._clock)
        rows = (
            await self.session.execute(
                select(IdentitySourceProfile, LogSource)
                .join(LogSource, LogSource.id == IdentitySourceProfile.log_source_id)
                .where(
                    IdentitySourceProfile.instance_id == instance.id,
                    IdentitySourceProfile.enabled.is_(True),
                )
                .order_by(IdentitySourceProfile.id)
            )
        ).all()
        for profile, source in rows:
            qradar_id = int(source.qradar_id)
            if _copy_report(profile).get("revalidation_slot") == slot:
                report.skipped += 1
                continue
            old = str(profile.validation_status)
            try:
                probe = await service.run_probe(profile, provider)
            except Exception as exc:
                token = failure_token(type(exc).__name__)
                await self._note_failure(profile, qradar_id, token, attempted, slot)
                report.probed += 1
                report.failed += 1
                report.sources.append(_source_row(qradar_id, old, old, 0))
                continue
            report.probed += 1
            report.searches += probe.searches
            reason = transport_reason(probe)
            if reason is not None:
                await self._note_failure(profile, qradar_id, reason, attempted, slot)
                report.failed += 1
                report.sources.append(_source_row(qradar_id, old, old, probe.searches))
                continue
            try:
                await service.store_probe(profile, probe, actor=REVALIDATION_ACTOR)
            except Exception as exc:
                token = failure_token(type(exc).__name__)
                await self._note_failure(profile, qradar_id, token, attempted, slot)
                report.failed += 1
                report.sources.append(_source_row(qradar_id, old, old, probe.searches))
                continue
            new = str(profile.validation_status)
            stamped = _copy_report(profile)
            stamped.pop("revalidation_error", None)
            stamped["revalidation_slot"] = slot
            stamped["revalidation_attempted_at"] = attempted
            profile.validation_report = stamped
            await record_audit(
                self.session,
                action="identity.profile.revalidated",
                actor=REVALIDATION_ACTOR,
                object_type=AUDIT_OBJECT,
                object_id=str(profile.id),
                detail={
                    "qradar_log_source_id": qradar_id,
                    "old_status": old,
                    "new_status": new,
                    "profile_version": profile.profile_version,
                },
            )
            if new != old:
                report.changed += 1
            report.sources.append(_source_row(qradar_id, old, new, probe.searches))
            logger.info(
                "identity revalidation source %s %s -> %s searches %d",
                qradar_id,
                old,
                new,
                probe.searches,
            )

    async def _note_failure(
        self,
        profile: IdentitySourceProfile,
        qradar_id: int,
        token: str,
        attempted: str,
        slot: int,
    ) -> None:
        """Record the failure. The previous status, version and ``validated_at`` stay."""
        merged = _copy_report(profile)
        merged["revalidation_error"] = token
        merged["revalidation_attempted_at"] = attempted
        merged["revalidation_slot"] = slot
        profile.validation_report = merged
        await record_audit(
            self.session,
            action="identity.profile.revalidation_failed",
            actor=REVALIDATION_ACTOR,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            outcome="FAILURE",
            detail={
                "qradar_log_source_id": qradar_id,
                "status": str(profile.validation_status),
                "reason": token,
            },
        )
        logger.warning(
            "identity revalidation failed source %s status %s reason %s",
            qradar_id,
            profile.validation_status,
            token,
        )


def _copy_report(profile: IdentitySourceProfile) -> dict[str, Any]:
    raw = profile.validation_report
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _source_row(qradar_id: int, old: str, new: str, searches: int) -> dict[str, Any]:
    return {
        "qradar_log_source_id": qradar_id,
        "old_status": old,
        "new_status": new,
        "searches": searches,
    }
