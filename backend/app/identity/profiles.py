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
    """The profile cannot be changed. The message does not echo operator text."""


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
        now = self._clock()
        seconds = min(
            self.settings.identity_validation_window_seconds, PROBE_MAX_WINDOW_SECONDS
        )
        end = now.astimezone(UTC)
        start = end - timedelta(seconds=seconds)
        probe = await provider.probe_identity_fields(
            qradar_log_source_id=await self._qradar_id(profile),
            event_id_property=profile.event_id_property,
            username_property=profile.username_property,
            window_start=start,
            window_end=end,
        )
        try:
            enabled = IdentityFieldMapping.from_profile(profile).enabled_event_ids
        except IdentityReferenceError as exc:
            raise IdentityProfileError(str(exc)) from exc
        result = evaluate_probe(
            probe, enabled, min_ratio=self.settings.identity_validation_min_ratio
        )
        profile.validation_status = result.status.value
        profile.validated_profile_version = profile.profile_version
        profile.validated_at = now
        profile.validation_report = result.report
        profile.validation_requested_at = now
        profile.validation_requested_by = actor
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="identity.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version, "status": result.status.value},
        )
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
