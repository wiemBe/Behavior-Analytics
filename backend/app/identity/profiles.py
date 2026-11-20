"""Administer identity source profiles: mapping, validation, enablement.

A mapping change bumps ``profile_version`` and clears validation. It does not
force ``enabled`` off; the collector still skips the row because the validated
version no longer matches. Enabling is refused until this version is VALID.
The audit detail has no query and no username.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.i18n.messages import MessageRef
from app.identity.dto import IdentityProbeResult
from app.identity.reference import (
    PROBE_MAX_WINDOW_SECONDS,
    IdentityFieldMapping,
    IdentityReferenceError,
)
from app.identity.validation import IdentityValidation, evaluate_probe
from app.models.enums import IdentityProfileValidationStatus
from app.models.identity_detection import IdentitySourceProfile
from app.models.log_source import LogSource
from app.providers.base import ProviderCapability, QRadarProvider
from app.repositories.audit import record_audit

AUDIT_OBJECT = "identity_source_profile"
_ENABLE_REFUSED = "profile version {version} is UNVALIDATED; run validate before enable"


class IdentityProfileError(Exception):
    """The profile cannot be changed. The message does not echo operator text.

    ``ref`` is the coded sentence when the refusal has one (a validation already running).
    """

    def __init__(self, message: str = "", *, ref: MessageRef | None = None) -> None:
        super().__init__(message)
        self.ref = ref


class IdentityProfileService:
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

    async def for_log_source(self, log_source: LogSource) -> IdentitySourceProfile | None:
        return await self.session.scalar(
            select(IdentitySourceProfile).where(
                IdentitySourceProfile.log_source_id == log_source.id
            )
        )

    async def set_mapping(
        self,
        *,
        log_source: LogSource,
        event_id_property: object,
        username_property: object,
        logon_type_property: object = None,
        enabled_event_ids: Sequence[int],
        actor: str,
        product_label: str | None = None,
    ) -> tuple[IdentitySourceProfile, bool]:
        """Create the profile, or change its mapping. Returns (profile, changed)."""
        mapping = IdentityFieldMapping.from_parts(
            event_id_property,
            username_property,
            logon_type_property,
            enabled_event_ids,
        )
        label = _product_label(product_label)
        profile = await self.for_log_source(log_source)
        if profile is None:
            profile = IdentitySourceProfile(
                instance_id=log_source.instance_id,
                log_source_id=log_source.id,
                enabled=False,
                profile_version=1,
                product_label=label,
                event_id_property=mapping.event_id_property,
                username_property=mapping.username_property,
                logon_type_property=mapping.logon_type_property,
                enabled_event_ids=list(mapping.enabled_event_ids),
                validation_status=IdentityProfileValidationStatus.UNVALIDATED.value,
                validation_report={},
                updated_by=actor,
            )
            self.session.add(profile)
            await self.session.flush()
            await record_audit(
                self.session,
                action="identity.profile.created",
                actor=actor,
                object_type=AUDIT_OBJECT,
                object_id=str(profile.id),
                detail={
                    "log_source_id": str(log_source.id),
                    "qradar_log_source_id": log_source.qradar_id,
                    "profile_version": 1,
                },
            )
            return profile, True

        current = IdentityFieldMapping.from_profile(profile)
        mapping_changed = _mapping_key(current) != _mapping_key(mapping)
        label_changed = label != profile.product_label
        if not mapping_changed and not label_changed:
            return profile, False
        previous = profile.profile_version
        if mapping_changed:
            profile.profile_version += 1
            profile.event_id_property = mapping.event_id_property
            profile.username_property = mapping.username_property
            profile.logon_type_property = mapping.logon_type_property
            profile.enabled_event_ids = list(mapping.enabled_event_ids)
            profile.validation_status = IdentityProfileValidationStatus.UNVALIDATED.value
            profile.validation_report = {}
            profile.validated_at = None
            profile.validated_profile_version = None
            profile.validation_requested_at = None
            profile.validation_requested_by = None
        if label_changed:
            profile.product_label = label
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action=(
                "identity.profile.mapping_changed"
                if mapping_changed
                else "identity.profile.relabelled"
            ),
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"previous_version": previous, "profile_version": profile.profile_version},
        )
        return profile, True

    async def enable(self, profile: IdentitySourceProfile, *, actor: str) -> bool:
        """Enable collection. Refused unless this version validated."""
        if (
            profile.validation_status != IdentityProfileValidationStatus.VALID.value
            or profile.validated_profile_version != profile.profile_version
        ):
            raise IdentityProfileError(_ENABLE_REFUSED.format(version=profile.profile_version))
        if profile.enabled:
            return False
        profile.enabled = True
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="identity.profile.enabled",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version},
        )
        return True

    async def disable(self, profile: IdentitySourceProfile, *, actor: str) -> bool:
        """Stop collection. The audit detail is the profile version only."""
        if not profile.enabled:
            return False
        profile.enabled = False
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="identity.profile.disabled",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version},
        )
        return True

    async def validate(
        self,
        profile: IdentitySourceProfile,
        provider: QRadarProvider,
        *,
        actor: str,
    ) -> IdentityValidation:
        """Run the bounded probe and store its result on this profile version."""
        if not provider.supports(ProviderCapability.AQL_EXECUTION):
            raise IdentityProfileError("this provider cannot run an identity validation probe")
        probe = await self.run_probe(profile, provider)
        return await self.store_validation(profile, probe, actor=actor)

    async def store_validation(
        self,
        profile: IdentitySourceProfile,
        probe: IdentityProbeResult,
        *,
        actor: str,
    ) -> IdentityValidation:
        """Store the probe's outcome and audit it. The pending marker is cleared."""
        result = await self.store_probe(profile, probe, actor=actor)
        await record_audit(
            self.session,
            action="identity.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version, "status": result.status.value},
        )
        return result

    async def request_validation(
        self,
        profile: IdentitySourceProfile,
        *,
        actor: str,
        enqueue: Callable[[str], object] | None = None,
    ) -> None:
        """Mark the profile pending, commit, then queue the probe.

        A request younger than ``identity_validation_pending_timeout_seconds`` is refused
        with the coded "already running" sentence. An older marker is a task that never
        reported back, so a new request replaces it.
        """
        now = self._clock()
        requested_at = profile.validation_requested_at
        if requested_at is not None:
            if requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=UTC)
            if now - requested_at < timedelta(
                seconds=self.settings.identity_validation_pending_timeout_seconds
            ):
                raise IdentityProfileError(
                    "a validation of this profile is already running",
                    ref=MessageRef(
                        "validation.profile.conflict.running",
                        {
                            "profile_version": profile.profile_version,
                            "since": requested_at.isoformat(),
                        },
                    ),
                )
        profile.validation_requested_at = now
        profile.validation_requested_by = actor
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="identity.profile.validation_requested",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version},
        )
        await self.session.commit()
        if enqueue is None:
            from app.workers.tasks import validate_identity_profile_task

            enqueue = validate_identity_profile_task.delay
        enqueue(str(profile.id))

    async def record_validation_failure(
        self,
        profile: IdentitySourceProfile,
        *,
        actor: str,
        reason: str,
        invalid: bool = False,
    ) -> None:
        """A probe that could not finish. It never stores VALID and clears the pending marker.

        ``invalid`` is for a mapping the profile itself refuses: the version is stored
        INVALID. Any other failure (the console, the provider, a missing source) leaves the
        stored status as it was and records the reason beside it, so a transient outage does
        not stop a collecting source.
        """
        now = self._clock()
        report = (
            dict(profile.validation_report) if isinstance(profile.validation_report, dict) else {}
        )
        report["validation_error"] = reason
        report["validation_attempted_at"] = now.astimezone(UTC).isoformat()
        if invalid:
            report["status"] = IdentityProfileValidationStatus.INVALID.value
            profile.validation_status = IdentityProfileValidationStatus.INVALID.value
            profile.validated_profile_version = profile.profile_version
            profile.validated_at = now
        profile.validation_report = report
        profile.validation_requested_at = None
        profile.validation_requested_by = None
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="identity.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            outcome="FAILURE",
            detail={"profile_version": profile.profile_version, "error": reason},
        )

    async def run_probe(
        self, profile: IdentitySourceProfile, provider: QRadarProvider
    ) -> IdentityProbeResult:
        """The same bounded probe ``validate`` runs. No status is written."""
        now = self._clock()
        seconds = min(
            self.settings.identity_validation_window_seconds, PROBE_MAX_WINDOW_SECONDS
        )
        end = now.astimezone(UTC)
        start = end - timedelta(seconds=seconds)
        return await provider.probe_identity_fields(
            qradar_log_source_id=await self._qradar_id(profile),
            event_id_property=profile.event_id_property,
            username_property=profile.username_property,
            window_start=start,
            window_end=end,
        )

    async def store_probe(
        self,
        profile: IdentitySourceProfile,
        probe: IdentityProbeResult,
        *,
        actor: str,
    ) -> IdentityValidation:
        """Write the evaluator's outcome. Does not audit and does not enable."""
        try:
            enabled = IdentityFieldMapping.from_profile(profile).enabled_event_ids
        except IdentityReferenceError as exc:
            raise IdentityProfileError(str(exc)) from exc
        now = self._clock()
        result = evaluate_probe(
            probe, enabled, min_ratio=self.settings.identity_validation_min_ratio
        )
        profile.validation_status = result.status.value
        profile.validated_profile_version = profile.profile_version
        profile.validated_at = now
        profile.validation_report = result.report
        profile.validation_requested_at = None
        profile.validation_requested_by = None
        profile.updated_by = actor
        await self.session.flush()
        return result

    async def _qradar_id(self, profile: IdentitySourceProfile) -> int:
        qradar_id = await self.session.scalar(
            select(LogSource.qradar_id).where(LogSource.id == profile.log_source_id)
        )
        if qradar_id is None:
            raise IdentityProfileError("the profile's log source is not in the inventory")
        return int(qradar_id)


def _product_label(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 128:
        raise IdentityProfileError("the product label is too long")
    return value


def _mapping_key(mapping: IdentityFieldMapping) -> tuple[object, ...]:
    return (
        mapping.event_id_property,
        mapping.username_property,
        mapping.logon_type_property,
        mapping.enabled_event_ids,
    )
