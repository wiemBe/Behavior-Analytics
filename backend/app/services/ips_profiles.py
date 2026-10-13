"""Administer and validate IPS source profiles without writing to QRadar."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.ips.profile import IpsFieldMapping, IpsProfileError
from app.ips.validation import IpsProfileValidation, evaluate_profile
from app.models.enums import IpsPreset, IpsProfileValidationStatus
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import IpsFieldProbeRequest, IpsSourceProbe
from app.repositories.audit import record_audit

AUDIT_OBJECT = "ips_source_profile"


class IpsProfileService:
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

    async def for_log_source(self, log_source: LogSource) -> IpsSourceProfile | None:
        return await self.session.scalar(
            select(IpsSourceProfile).where(IpsSourceProfile.log_source_id == log_source.id)
        )

    async def upsert(
        self,
        *,
        log_source: LogSource,
        mapping: IpsFieldMapping,
        actor: str,
        product_label: str | None = None,
        preset: IpsPreset | None = None,
        replace_metadata: bool = False,
    ) -> tuple[IpsSourceProfile, bool]:
        after = mapping.as_dict()
        profile = await self.for_log_source(log_source)
        if profile is None:
            profile = IpsSourceProfile(
                instance_id=log_source.instance_id,
                log_source_id=log_source.id,
                enabled=False,
                profile_version=1,
                product_label=product_label,
                preset=preset,
                validation_status=IpsProfileValidationStatus.UNVALIDATED,
                validation_report={},
                updated_by=actor,
            )
            mapping.apply_to(profile)
            self.session.add(profile)
            await self.session.flush()
            await record_audit(
                self.session,
                action="ips.profile.created",
                actor=actor,
                object_type=AUDIT_OBJECT,
                object_id=str(profile.id),
                detail={
                    "log_source_id": str(log_source.id),
                    "qradar_log_source_id": log_source.qradar_id,
                    "profile_version": 1,
                    "mapping": after,
                    "preset": preset.value if preset else None,
                },
            )
            return profile, True

        before = IpsFieldMapping.from_profile(profile).as_dict()
        mapping_changed = before != after
        metadata_changed = (
            replace_metadata or product_label is not None or preset is not None
        ) and (product_label != profile.product_label or preset != profile.preset)
        if not mapping_changed and not metadata_changed:
            return profile, False

        previous_version = profile.profile_version
        if mapping_changed:
            profile.profile_version += 1
            mapping.apply_to(profile)
            profile.validation_status = IpsProfileValidationStatus.UNVALIDATED
            profile.validation_report = {}
            profile.validated_at = None
            profile.validation_requested_at = None
            profile.validation_requested_by = None
        if metadata_changed:
            profile.product_label = product_label
            profile.preset = preset
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action=(
                "ips.profile.mapping_changed" if mapping_changed else "ips.profile.relabelled"
            ),
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={
                "previous_version": previous_version,
                "profile_version": profile.profile_version,
                "mapping_before": before,
                "mapping_after": after,
                "preset": preset.value if preset else None,
            },
        )
        return profile, True

    async def set_enabled(
        self, profile: IpsSourceProfile, enabled: bool, *, actor: str
    ) -> bool:
        if enabled and (
            profile.validation_status != IpsProfileValidationStatus.VALID
            or profile.validated_profile_version != profile.profile_version
        ):
            raise IpsProfileError(
                f"profile version {profile.profile_version} has not validated "
                f"(status {profile.validation_status}); run validation first"
            )
        if profile.enabled == enabled:
            return False
        profile.enabled = enabled
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="ips.profile.enabled" if enabled else "ips.profile.disabled",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version},
        )
        return True

    async def validate(
        self,
        profile: IpsSourceProfile,
        provider: QRadarProvider,
        *,
        actor: str,
        persist: bool = True,
    ) -> IpsProfileValidation:
        now = self._clock()
        window_end = now.replace(microsecond=0)
        window_start = window_end - timedelta(seconds=self.settings.ips_validation_window_seconds)
        log_source = await self.session.get(LogSource, profile.log_source_id)
        if log_source is None:
            raise IpsProfileError("the profile's log source no longer exists locally")
        mapping = IpsFieldMapping.from_profile(profile)

        lookup_error: str | None = None
        found = False
        try:
            found = await provider.get_log_source(log_source.qradar_id) is not None
        except ProviderError as exc:
            lookup_error = f"{type(exc).__name__}: {exc}"[:400]
        if found:
            fields = [
                IpsFieldProbeRequest(
                    logical_field=logical.value,
                    reference_kind=reference.kind.value,
                    name=reference.name,
                )
                for logical, reference in mapping.mapped().items()
            ]
            probe = await provider.probe_ips_fields(
                qradar_log_source_id=log_source.qradar_id,
                window_start=window_start,
                window_end=window_end,
                fields=fields,
                max_action_values=self.settings.ips_validation_max_action_values,
            )
        else:
            probe = IpsSourceProbe(
                qradar_log_source_id=log_source.qradar_id,
                window_start=window_start,
                window_end=window_end,
                total_error="not probed: the log source was not confirmed in QRadar",
            )
        result = evaluate_profile(
            mapping,
            probe,
            log_source_found=found,
            min_ratio=self.settings.ips_validation_min_ratio,
            log_source_error=lookup_error,
        )
        if persist:
            await self.store_validation(profile, result, actor=actor, validated_at=now)
        return result

    async def store_validation(
        self,
        profile: IpsSourceProfile,
        result: IpsProfileValidation,
        *,
        actor: str,
        validated_at: datetime | None = None,
    ) -> None:
        now = validated_at or self._clock()
        profile.validation_status = result.status
        profile.validated_profile_version = profile.profile_version
        profile.validated_at = now
        profile.validation_report = {
            **result.as_report(),
            "profile_version": profile.profile_version,
            "validated_at": now.isoformat(),
        }
        profile.validation_requested_at = None
        profile.validation_requested_by = None
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="ips.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            outcome=(
                "SUCCESS"
                if result.status is IpsProfileValidationStatus.VALID
                else "FAILURE"
            ),
            detail={
                "profile_version": profile.profile_version,
                "status": result.status.value,
                "reasons": result.reasons,
            },
        )

    async def request_validation(
        self,
        profile: IpsSourceProfile,
        *,
        actor: str,
        enqueue: Callable[[str], object] | None = None,
    ) -> None:
        now = self._clock()
        requested_at = profile.validation_requested_at
        if requested_at is not None:
            if requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=UTC)
            if now - requested_at < timedelta(
                seconds=self.settings.ips_validation_pending_timeout_seconds
            ):
                raise IpsProfileError(
                    f"validation of version {profile.profile_version} is already running "
                    f"since {requested_at.isoformat()}"
                )
        profile.validation_requested_at = now
        profile.validation_requested_by = actor
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="ips.profile.validation_requested",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version},
        )
        await self.session.commit()
        if enqueue is None:
            from app.workers.tasks import validate_ips_profile_task

            enqueue = validate_ips_profile_task.delay
        enqueue(str(profile.id))

    async def record_validation_exception(
        self,
        profile: IpsSourceProfile,
        *,
        actor: str,
        error_class: str,
    ) -> None:
        profile.validation_requested_at = None
        profile.validation_requested_by = None
        await self.session.flush()
        await record_audit(
            self.session,
            action="ips.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            outcome="FAILURE",
            detail={
                "profile_version": profile.profile_version,
                "error_class": error_class,
            },
        )

    async def record_validation_source_busy(
        self,
        profile: IpsSourceProfile,
        *,
        actor: str,
        retry_count: int,
    ) -> None:
        profile.validation_requested_at = None
        profile.validation_requested_by = None
        await self.session.flush()
        await record_audit(
            self.session,
            action="ips.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            outcome="SOURCE_BUSY",
            detail={
                "profile_version": profile.profile_version,
                "status": "SOURCE_BUSY",
                "retry_count": retry_count,
            },
        )
