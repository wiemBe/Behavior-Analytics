"""Judge a WAF profile against bounded QRadar evidence. Pure; no I/O.

A query that succeeds proves only that QRadar accepted it. Whether a logical
field is really available on a source is a separate question with several wrong
answers, so each field gets exactly one of the `WafFieldStatus` outcomes:

  * not mapped                      -> NOT_MAPPED
  * QRadar rejected the property    -> PROPERTY_REJECTED (proof it does not exist)
  * the query failed otherwise      -> QUERY_FAILED      (proves nothing)
  * the source had no events        -> NO_EVENTS         (proves nothing)
  * no event carried a value        -> UNPOPULATED
  * too small a share carried one   -> INSUFFICIENT_COVERAGE
  * too few values look right       -> IMPLAUSIBLE
  * otherwise                       -> AVAILABLE

Coverage and plausibility thresholds apply only to fields collection reads.
Optional fields are reported with their measured shares but judged only on
existence and plausibility, because an optional field populated on some events
is still useful evidence.

Only bounded enumerations -- method, action, response code -- are carried into
the persisted report as discovered values. URLs, virtual hosts, rule ids and
user agents are reported as counts; their values are discarded here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import WafFieldStatus, WafLogicalField, WafProfileValidationStatus
from app.providers.dto import WafPropertyProbe, WafSourceProbe
from app.waf.normalize import (
    normalize_endpoint,
    normalize_method,
    normalize_response_code,
    normalize_virtual_host,
)
from app.waf.profile import WafFieldMapping

_TOKEN_MAX = 64


def _bounded_token(value: str) -> bool:
    text = value.strip()
    return 0 < len(text) <= _TOKEN_MAX and text.isprintable()


PLAUSIBLE: Mapping[WafLogicalField, Callable[[str], bool]] = {
    WafLogicalField.URL: lambda v: normalize_endpoint(v).is_usable,
    WafLogicalField.METHOD: lambda v: normalize_method(v) is not None,
    WafLogicalField.VIRTUAL_HOST: lambda v: normalize_virtual_host(v) is not None,
    WafLogicalField.RESPONSE_CODE: lambda v: normalize_response_code(v) is not None,
    WafLogicalField.ACTION: _bounded_token,
    WafLogicalField.RULE_ID: _bounded_token,
    WafLogicalField.USER_AGENT: lambda v: 0 < len(v.strip()) <= 1024,
}

#: Low-cardinality enumerations safe to record verbatim in a report.
DISCLOSABLE = frozenset(
    {WafLogicalField.METHOD, WafLogicalField.ACTION, WafLogicalField.RESPONSE_CODE}
)
_DISCLOSED_VALUES_MAX = 20

#: Statuses that prove a collection field unusable.
_INVALIDATING = frozenset(
    {
        WafFieldStatus.NOT_MAPPED,
        WafFieldStatus.PROPERTY_REJECTED,
        WafFieldStatus.UNPOPULATED,
        WafFieldStatus.INSUFFICIENT_COVERAGE,
        WafFieldStatus.IMPLAUSIBLE,
    }
)


@dataclass
class FieldValidation:
    logical: WafLogicalField
    property_name: str | None
    status: WafFieldStatus
    used_by_collection: bool
    populated_events: int | None = None
    populated_ratio: float | None = None
    distinct_values: int | None = None
    #: Events among the sampled top values that passed the plausibility check.
    plausible_ratio: float | None = None
    sampled_events: int | None = None
    values_truncated: bool = False
    discovered_values: list[dict[str, Any]] = field(default_factory=list)
    detail: str | None = None
    queries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.logical.value,
            "property_name": self.property_name,
            "status": self.status.value,
            "used_by_collection": self.used_by_collection,
            "populated_events": self.populated_events,
            "populated_ratio": self.populated_ratio,
            "distinct_values": self.distinct_values,
            "plausible_ratio": self.plausible_ratio,
            "sampled_events": self.sampled_events,
            "values_truncated": self.values_truncated,
            "discovered_values": self.discovered_values,
            "detail": self.detail,
            "queries": self.queries,
        }


@dataclass
class ProfileValidation:
    status: WafProfileValidationStatus
    reasons: list[str]
    log_source_found: bool
    total_events: int | None
    fields: list[FieldValidation]
    window_start: str
    window_end: str
    total_query: str | None = None

    def field_status(self, logical: WafLogicalField) -> WafFieldStatus:
        """Test-only lookup of one field's status on a validation result."""
        return next(f.status for f in self.fields if f.logical is logical)

    def as_report(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reasons": self.reasons,
            "log_source_found": self.log_source_found,
            "total_events": self.total_events,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "total_query": self.total_query,
            "fields": [f.as_dict() for f in self.fields],
        }


def evaluate_field(
    logical: WafLogicalField,
    property_name: str | None,
    probe: WafPropertyProbe | None,
    *,
    total_events: int | None,
    used_by_collection: bool,
    min_ratio: float,
) -> FieldValidation:
    result = FieldValidation(
        logical=logical,
        property_name=property_name,
        status=WafFieldStatus.AVAILABLE,
        used_by_collection=used_by_collection,
    )
    if property_name is None or probe is None:
        result.status = WafFieldStatus.NOT_MAPPED
        result.detail = "no QRadar property is mapped to this field"
        return result

    result.queries = list(probe.queries)
    if probe.rejected:
        result.status = WafFieldStatus.PROPERTY_REJECTED
        result.detail = "QRadar rejected a query naming this property; it does not exist as named"
        return result
    if probe.error is not None or probe.populated_events is None:
        result.status = WafFieldStatus.QUERY_FAILED
        result.detail = probe.error or "population was not measured"
        return result
    if total_events is None:
        result.status = WafFieldStatus.QUERY_FAILED
        result.detail = "the source's total event volume was not measured"
        return result
    if total_events == 0:
        result.status = WafFieldStatus.NO_EVENTS
        result.detail = "the source produced no events in the window; nothing can be concluded"
        return result

    result.populated_events = probe.populated_events
    result.populated_ratio = round(min(1.0, probe.populated_events / total_events), 4)
    result.distinct_values = probe.distinct_values
    result.values_truncated = probe.values_truncated

    if probe.populated_events == 0:
        result.status = WafFieldStatus.UNPOPULATED
        result.detail = "the property exists but no event in the window carried a value"
        return result

    check = PLAUSIBLE[logical]
    sampled = sum(v.events for v in probe.values)
    plausible = sum(v.events for v in probe.values if check(v.value))
    result.sampled_events = sampled
    result.plausible_ratio = round(plausible / sampled, 4) if sampled else None
    if logical in DISCLOSABLE:
        result.discovered_values = [
            {"value": v.value.strip()[:_TOKEN_MAX], "events": v.events}
            for v in probe.values[:_DISCLOSED_VALUES_MAX]
            if _bounded_token(v.value)
        ]

    if used_by_collection and result.populated_ratio < min_ratio:
        result.status = WafFieldStatus.INSUFFICIENT_COVERAGE
        result.detail = (
            f"populated on {result.populated_ratio:.1%} of events, below the "
            f"required {min_ratio:.0%}"
        )
        return result
    if result.plausible_ratio is None or result.plausible_ratio < min_ratio:
        result.status = WafFieldStatus.IMPLAUSIBLE
        shown = "no values" if result.plausible_ratio is None else f"{result.plausible_ratio:.1%}"
        result.detail = (
            f"{shown} of sampled events carry a plausible {logical.value.lower()} value, "
            f"below the required {min_ratio:.0%}"
        )
        return result
    return result


def evaluate_profile(
    mapping: WafFieldMapping,
    probe: WafSourceProbe,
    *,
    log_source_found: bool,
    min_ratio: float,
    log_source_error: str | None = None,
) -> ProfileValidation:
    by_name = {p.property_name: p for p in probe.properties}
    collection_fields = mapping.collection_fields()
    fields = [
        evaluate_field(
            logical,
            mapping.property_for(logical),
            by_name.get(mapping.property_for(logical) or ""),
            total_events=probe.total_events,
            used_by_collection=logical in collection_fields,
            min_ratio=min_ratio,
        )
        for logical in WafLogicalField
    ]

    reasons: list[str] = []
    status = WafProfileValidationStatus.VALID
    if log_source_error is not None:
        status = WafProfileValidationStatus.INCONCLUSIVE
        reasons.append(f"the log source lookup failed ({log_source_error})")
    elif not log_source_found:
        status = WafProfileValidationStatus.INVALID
        reasons.append("the log source does not exist in QRadar")
    for f in fields:
        if not f.used_by_collection:
            continue
        if f.status in _INVALIDATING:
            status = WafProfileValidationStatus.INVALID
            reasons.append(f"{f.logical.value}: {f.status.value} ({f.detail})")
        elif f.status is not WafFieldStatus.AVAILABLE and status is not (
            WafProfileValidationStatus.INVALID
        ):
            status = WafProfileValidationStatus.INCONCLUSIVE
            reasons.append(f"{f.logical.value}: {f.status.value} ({f.detail})")
    if probe.total_error is not None and status is WafProfileValidationStatus.VALID:
        status = WafProfileValidationStatus.INCONCLUSIVE
        reasons.append(f"total event volume was not measured ({probe.total_error})")

    return ProfileValidation(
        status=status,
        reasons=reasons,
        log_source_found=log_source_found,
        total_events=probe.total_events,
        fields=fields,
        window_start=probe.window_start.isoformat(),
        window_end=probe.window_end.isoformat(),
        total_query=probe.total_query,
    )
