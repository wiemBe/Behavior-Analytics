"""Pure IPS profile validation over bounded provider aggregates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.text import percent
from app.i18n.messages import MessageRef, render
from app.ips.profile import IpsFieldMapping, IpsFieldReference
from app.models.enums import IpsFieldStatus, IpsLogicalField, IpsProfileValidationStatus
from app.providers.dto import IpsFieldProbe, IpsSourceProbe

_NOT_MAPPED = MessageRef("validation.ips.field.not_mapped")
_FIELD_REJECTED = MessageRef("validation.ips.field.rejected")
_POPULATION_NOT_MEASURED = MessageRef("validation.field.population_not_measured")
_TOTAL_NOT_MEASURED = MessageRef("validation.field.total_not_measured")
_NO_EVENTS = MessageRef("validation.field.no_events")
_UNPOPULATED = MessageRef("validation.ips.field.unpopulated")


@dataclass
class IpsFieldValidation:
    logical: IpsLogicalField
    reference: IpsFieldReference | None
    status: IpsFieldStatus
    used_by_collection: bool
    populated_events: int | None = None
    populated_ratio: float | None = None
    generic_name_events: int | None = None
    generic_name_ratio: float | None = None
    values_truncated: bool = False
    discovered_values: list[dict[str, Any]] = field(default_factory=list)
    detail: str | None = None
    detail_message: MessageRef | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "field": self.logical.value,
            "reference": self.reference.as_dict() if self.reference else None,
            "status": self.status.value,
            "used_by_collection": self.used_by_collection,
            "populated_events": self.populated_events,
            "populated_ratio": self.populated_ratio,
            "generic_name_events": self.generic_name_events,
            "generic_name_ratio": self.generic_name_ratio,
            "values_truncated": self.values_truncated,
            "discovered_values": self.discovered_values,
            "detail": self.detail,
            "warnings": self.warnings,
        }
        if self.detail_message is not None:
            wire = self.detail_message.to_wire()
            out["detail_code"] = wire["code"]
            out["detail_params"] = wire["params"]
        return out


@dataclass
class IpsProfileValidation:
    status: IpsProfileValidationStatus
    reasons: list[str]
    reason_messages: list[MessageRef | None]
    warnings: list[str]
    log_source_found: bool
    total_events: int | None
    fields: list[IpsFieldValidation]
    window_start: str
    window_end: str
    probe_truncated: bool
    ariel_searches: int

    def field_status(self, logical: IpsLogicalField) -> IpsFieldStatus:
        """Test-only lookup of one field's status on a validation result."""
        return next(field.status for field in self.fields if field.logical is logical)

    def as_report(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reasons": self.reasons,
            "reason_codes": [
                message.to_wire() if message is not None else None
                for message in self.reason_messages
            ],
            "warnings": self.warnings,
            "log_source_found": self.log_source_found,
            "total_events": self.total_events,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "probe_truncated": self.probe_truncated,
            "ariel_searches": self.ariel_searches,
            "fields": [field.as_dict() for field in self.fields],
        }


def evaluate_field(
    logical: IpsLogicalField,
    reference: IpsFieldReference | None,
    probe: IpsFieldProbe | None,
    *,
    total_events: int | None,
    used_by_collection: bool,
    min_ratio: float,
) -> IpsFieldValidation:
    result = IpsFieldValidation(
        logical=logical,
        reference=reference,
        status=IpsFieldStatus.AVAILABLE,
        used_by_collection=used_by_collection,
    )
    if reference is None or probe is None:
        result.status = IpsFieldStatus.NOT_MAPPED
        result.detail = render(_NOT_MAPPED)
        result.detail_message = _NOT_MAPPED
        return result
    if probe.rejected:
        result.status = IpsFieldStatus.FIELD_REJECTED
        result.detail = render(_FIELD_REJECTED)
        result.detail_message = _FIELD_REJECTED
        return result
    if probe.error is not None or probe.populated_events is None:
        result.status = IpsFieldStatus.QUERY_FAILED
        if probe.error is not None:
            result.detail = probe.error
        else:
            result.detail = render(_POPULATION_NOT_MEASURED)
            result.detail_message = _POPULATION_NOT_MEASURED
        return result
    if total_events is None:
        result.status = IpsFieldStatus.QUERY_FAILED
        result.detail = render(_TOTAL_NOT_MEASURED)
        result.detail_message = _TOTAL_NOT_MEASURED
        return result
    if total_events == 0:
        result.status = IpsFieldStatus.NO_EVENTS
        result.detail = render(_NO_EVENTS)
        result.detail_message = _NO_EVENTS
        return result

    result.populated_events = probe.populated_events
    result.populated_ratio = round(min(1.0, probe.populated_events / total_events), 4)
    result.values_truncated = probe.values_truncated
    result.discovered_values = [
        {"value": value.value, "events": value.events} for value in probe.action_values
    ]
    if probe.generic_name_events is not None:
        result.generic_name_events = probe.generic_name_events
        result.generic_name_ratio = round(
            min(1.0, probe.generic_name_events / total_events), 4
        )
        if probe.generic_name_events > 0:
            warning = (
                f"{logical.value}: {percent(result.generic_name_ratio, precision=1)} of events "
                "use a generic Unknown QID name"
            )
            result.warnings.append(warning)

    if probe.populated_events == 0:
        result.status = IpsFieldStatus.UNPOPULATED
        result.detail = render(_UNPOPULATED)
        result.detail_message = _UNPOPULATED
    elif used_by_collection and result.populated_ratio < min_ratio:
        result.status = IpsFieldStatus.INSUFFICIENT_COVERAGE
        result.detail_message = MessageRef(
            "validation.field.insufficient_coverage",
            {"populated": result.populated_ratio, "required": min_ratio},
        )
        result.detail = render(result.detail_message)
    return result


def evaluate_profile(
    mapping: IpsFieldMapping,
    probe: IpsSourceProbe,
    *,
    log_source_found: bool,
    min_ratio: float,
    log_source_error: str | None = None,
) -> IpsProfileValidation:
    by_field = {field.logical_field: field for field in probe.fields}
    collection_fields = mapping.collection_fields()
    fields = [
        evaluate_field(
            logical,
            mapping.reference_for(logical),
            by_field.get(logical.value),
            total_events=probe.total_events,
            used_by_collection=logical in collection_fields,
            min_ratio=min_ratio,
        )
        for logical in IpsLogicalField
    ]
    reasons: list[str] = []
    reason_messages: list[MessageRef | None] = []

    def add_reason(message: MessageRef) -> None:
        reasons.append(render(message))
        reason_messages.append(message)

    warnings = [warning for item in fields for warning in item.warnings]
    status = IpsProfileValidationStatus.VALID
    if log_source_error is not None:
        status = IpsProfileValidationStatus.INCONCLUSIVE
        add_reason(
            MessageRef("validation.profile.lookup_failed", {"error": log_source_error})
        )
    elif not log_source_found:
        status = IpsProfileValidationStatus.INVALID
        add_reason(MessageRef("validation.profile.source_missing"))
    if (
        probe.total_error is not None
        and status is not IpsProfileValidationStatus.INVALID
    ):
        status = IpsProfileValidationStatus.INCONCLUSIVE
        add_reason(
            MessageRef(
                "validation.profile.total_not_measured",
                {"error": probe.total_error},
            )
        )
    if probe.truncated and status is not IpsProfileValidationStatus.INVALID:
        status = IpsProfileValidationStatus.INCONCLUSIVE
        add_reason(MessageRef("validation.ips.profile.truncated"))

    for item in fields:
        if item.reference is None:
            continue
        if item.status is IpsFieldStatus.FIELD_REJECTED:
            status = IpsProfileValidationStatus.INVALID
            add_reason(
                MessageRef(
                    "validation.profile.field_reason",
                    {
                        "field": item.logical.value,
                        "status": item.status.value,
                        "detail": item.detail_message or item.detail or "",
                    },
                )
            )
        elif item.status in {IpsFieldStatus.QUERY_FAILED, IpsFieldStatus.NO_EVENTS}:
            if status is not IpsProfileValidationStatus.INVALID:
                status = IpsProfileValidationStatus.INCONCLUSIVE
            add_reason(
                MessageRef(
                    "validation.profile.field_reason",
                    {
                        "field": item.logical.value,
                        "status": item.status.value,
                        "detail": item.detail_message or item.detail or "",
                    },
                )
            )
        elif item.used_by_collection and item.status in {
            IpsFieldStatus.UNPOPULATED,
            IpsFieldStatus.INSUFFICIENT_COVERAGE,
        }:
            status = IpsProfileValidationStatus.INVALID
            add_reason(
                MessageRef(
                    "validation.profile.field_reason",
                    {
                        "field": item.logical.value,
                        "status": item.status.value,
                        "detail": item.detail_message or item.detail or "",
                    },
                )
            )

    return IpsProfileValidation(
        status=status,
        reasons=reasons,
        reason_messages=reason_messages,
        warnings=warnings,
        log_source_found=log_source_found,
        total_events=probe.total_events,
        fields=fields,
        window_start=probe.window_start.isoformat(),
        window_end=probe.window_end.isoformat(),
        probe_truncated=probe.truncated,
        ariel_searches=probe.ariel_searches,
    )
