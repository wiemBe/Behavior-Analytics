"""Administer WAF source profiles: mapping, enablement, validation.

Every change is written to the append-only audit log with its before and after
state. A mapping change bumps `profile_version` and resets validation, so a
source never keeps collecting on a mapping nobody has checked against QRadar.

Validation is read-only against QRadar: one log-source lookup and a bounded
probe of the mapped properties over a small window. Its report stores counts,
statuses, query text and bounded enumerations only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import WafProfileValidationStatus
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import WafSourceProbe
from app.repositories.audit import record_audit
from app.waf.profile import WafFieldMapping, WafProfileError
from app.waf.validation import ProfileValidation, evaluate_profile

AUDIT_OBJECT = "waf_source_profile"


class WafProfileService:
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

    async def for_log_source(self, log_source: LogSource) -> WafSourceProfile | None:
        return await self.session.scalar(
            select(WafSourceProfile).where(WafSourceProfile.log_source_id == log_source.id)
        )

    async def upsert(
        self,
        *,
        log_source: LogSource,
        mapping: WafFieldMapping,
        actor: str,
        product_label: str | None = None,
    ) -> tuple[WafSourceProfile, bool]:
        """Create the profile, or change its mapping. Returns (profile, changed)."""
        after = mapping.as_dict()
        profile = await self.for_log_source(log_source)
        if profile is None:
            profile = WafSourceProfile(
                instance_id=log_source.instance_id,
                log_source_id=log_source.id,
                enabled=False,
                profile_version=1,
                product_label=product_label,
                validation_status=WafProfileValidationStatus.UNVALIDATED,
                validation_report={},
                updated_by=actor,
            )
            mapping.apply_to(profile)
            self.session.add(profile)
            await self.session.flush()
            await record_audit(
                self.session,
                action="waf.profile.created",
                actor=actor,
                object_type=AUDIT_OBJECT,
                object_id=str(profile.id),
                detail={
                    "log_source_id": str(log_source.id),
                    "qradar_log_source_id": log_source.qradar_id,
                    "profile_version": 1,
                    "mapping": after,
                },
            )
            return profile, True

        before = WafFieldMapping.from_profile(profile).as_dict()
        mapping_changed = before != after
        label_changed = product_label is not None and product_label != profile.product_label
        if not mapping_changed and not label_changed:
            return profile, False

        previous_version = profile.profile_version
        if mapping_changed:
            profile.profile_version += 1
            mapping.apply_to(profile)
            # The old validation said nothing about the new mapping.
            profile.validation_status = WafProfileValidationStatus.UNVALIDATED
            profile.validation_report = {}
            profile.validated_at = None
        if label_changed:
            profile.product_label = product_label
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="waf.profile.mapping_changed" if mapping_changed else "waf.profile.relabelled",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={
                "previous_version": previous_version,
                "profile_version": profile.profile_version,
                "mapping_before": before,
                "mapping_after": after,
            },
        )
        return profile, True

    async def set_enabled(
        self, profile: WafSourceProfile, enabled: bool, *, actor: str
    ) -> bool:
        """Enable or disable scheduled collection. Returns whether it changed.

        Enabling is refused unless the *current* version validated. A disable is
        always allowed.
        """
        if enabled and (
            profile.validation_status != WafProfileValidationStatus.VALID
            or profile.validated_profile_version != profile.profile_version
        ):
            raise WafProfileError(
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
            action="waf.profile.enabled" if enabled else "waf.profile.disabled",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            detail={"profile_version": profile.profile_version},
        )
        return True

    async def validate(
        self, profile: WafSourceProfile, provider: QRadarProvider, *, actor: str
    ) -> ProfileValidation:
        """Check the profile against QRadar and record the structured result."""
        now = self._clock()
        window_end = now.replace(microsecond=0)
        window_start = window_end - timedelta(seconds=self.settings.waf_validation_window_seconds)
        log_source = await self.session.get(LogSource, profile.log_source_id)
        if log_source is None:
            raise WafProfileError("the profile's log source no longer exists locally")
        mapping = WafFieldMapping.from_profile(profile)

        lookup_error: str | None = None
        found = False
        try:
            found = await provider.get_log_source(log_source.qradar_id) is not None
        except ProviderError as exc:
            lookup_error = f"{type(exc).__name__}: {exc}"[:400]

        if found:
            probe = await provider.probe_waf_properties(
                qradar_log_source_id=log_source.qradar_id,
                window_start=window_start,
                window_end=window_end,
                property_names=list(mapping.mapped().values()),
                max_values=self.settings.waf_validation_max_values,
            )
        else:
            # No Ariel query is spent on a source QRadar did not confirm.
            probe = WafSourceProbe(
                qradar_log_source_id=log_source.qradar_id,
                window_start=window_start,
                window_end=window_end,
                total_error="not probed: the log source was not confirmed in QRadar",
            )

        result = evaluate_profile(
            mapping,
            probe,
            log_source_found=found,
            min_ratio=self.settings.waf_validation_min_ratio,
            log_source_error=lookup_error,
        )
        profile.validation_status = result.status
        profile.validated_profile_version = profile.profile_version
        profile.validated_at = now
        profile.validation_report = {
            **result.as_report(),
            "profile_version": profile.profile_version,
            "validated_at": now.isoformat(),
        }
        profile.updated_by = actor
        await self.session.flush()
        await record_audit(
            self.session,
            action="waf.profile.validated",
            actor=actor,
            object_type=AUDIT_OBJECT,
            object_id=str(profile.id),
            outcome="SUCCESS" if result.status is WafProfileValidationStatus.VALID else "FAILURE",
            detail={
                "profile_version": profile.profile_version,
                "status": result.status.value,
                "reasons": result.reasons,
            },
        )
        return result
