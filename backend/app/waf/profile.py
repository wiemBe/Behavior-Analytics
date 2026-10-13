"""WAF source profiles: the logical field mapping, and when a source may run.

Business logic reads logical fields -- URL, method, virtual host -- never
QRadar property names. The names come from the source's profile, because they
are authored per site and differ between WAF products and DSMs. No property
name appears as a default anywhere in collection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.models.enums import (
    WafBreakdown,
    WafFieldStatus,
    WafLogicalField,
    WafProfileValidationStatus,
)
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.providers.base import PROPERTY_NAME_PATTERN

#: Fields collection cannot run without.
REQUIRED_FIELDS = frozenset({WafLogicalField.URL, WafLogicalField.METHOD})

_COLUMN_BY_FIELD: dict[WafLogicalField, str] = {
    WafLogicalField.URL: "url_property",
    WafLogicalField.METHOD: "method_property",
    WafLogicalField.VIRTUAL_HOST: "virtual_host_property",
    WafLogicalField.ACTION: "action_property",
    WafLogicalField.RESPONSE_CODE: "response_code_property",
    WafLogicalField.RULE_ID: "rule_id_property",
    WafLogicalField.USER_AGENT: "user_agent_property",
}


@dataclass(frozen=True)
class WafMappingField:
    """One UI-editable logical field in the WAF mapping contract."""

    key: WafLogicalField
    label: str
    description: str
    required: bool
    used_by_collection: bool


#: The API and editor read this declaration; labels and help copy do not have a
#: second source in a route or frontend fixture.
WAF_MAPPING_FIELDS = (
    WafMappingField(
        WafLogicalField.URL,
        "URL path",
        "The request path. Normalized into the endpoint every WAF metric is counted for.",
        True,
        True,
    ),
    WafMappingField(
        WafLogicalField.METHOD,
        "HTTP method",
        "Part of the endpoint key, e.g. GET /login.",
        True,
        True,
    ),
    WafMappingField(
        WafLogicalField.VIRTUAL_HOST,
        "Virtual host",
        "Separates applications that share a path. Unmapped, endpoints combine their traffic.",
        False,
        True,
    ),
    WafMappingField(
        WafLogicalField.ACTION,
        "Action",
        "What the WAF did. Feeds the block ratio, through the action value map.",
        False,
        False,
    ),
    WafMappingField(
        WafLogicalField.RESPONSE_CODE,
        "HTTP status",
        "Feeds the 4xx and 5xx ratios.",
        False,
        False,
    ),
    WafMappingField(
        WafLogicalField.RULE_ID,
        "WAF rule id",
        "Counted for evidence only.",
        False,
        False,
    ),
    WafMappingField(
        WafLogicalField.USER_AGENT,
        "User agent",
        "Counted for cardinality only; values are never stored.",
        False,
        False,
    ),
)


class WafProfileError(ValueError):
    """A profile operation was refused. The message is safe to show an operator."""


def validate_property_name(name: str) -> str:
    """Accept a property name exactly as written, or refuse it.

    No trimming, quoting or expression is accepted: the name reaches AQL
    verbatim inside double quotes, so anything outside the allowlist is refused
    rather than repaired.
    """
    if not PROPERTY_NAME_PATTERN.match(name):
        raise WafProfileError(
            f"invalid QRadar property name {name!r}: letters, digits, underscore, "
            "space and hyphen only, at most 64 characters"
        )
    return name


#: Outcomes an action value can be mapped to. Anything else is unmapped.
ACTION_OUTCOMES = ("BLOCKED", "ALLOWED")
_ACTION_VALUE_MAX = 64


def action_key(value: str) -> str:
    """How a raw action value is compared with the map: trimmed, case-folded."""
    return value.strip().casefold()


def _action_values(values: Iterable[str], outcome: str) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values:
        value = raw.strip()
        if not (0 < len(value) <= _ACTION_VALUE_MAX and value.isprintable()):
            raise WafProfileError(
                f"invalid {outcome} action value {raw!r}: a printable token of at most "
                f"{_ACTION_VALUE_MAX} characters"
            )
        if action_key(value) not in {action_key(v) for v in out}:
            out.append(value)
    return tuple(sorted(out, key=action_key))


@dataclass(frozen=True)
class WafFieldMapping:
    """Logical field -> QRadar property name. None means unavailable.

    `blocked_actions` / `allowed_actions` are the site's action values by
    outcome, authored from values validation discovered. They are part of the
    mapping: changing them changes what `blocked_count` means, so it bumps the
    profile version exactly as a property change does.
    """

    url: str
    method: str
    virtual_host: str | None = None
    action: str | None = None
    response_code: str | None = None
    rule_id: str | None = None
    user_agent: str | None = None
    blocked_actions: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in self.mapped().values():
            validate_property_name(name)
        blocked = _action_values(self.blocked_actions, "BLOCKED")
        allowed = _action_values(self.allowed_actions, "ALLOWED")
        object.__setattr__(self, "blocked_actions", blocked)
        object.__setattr__(self, "allowed_actions", allowed)
        overlap = {action_key(v) for v in blocked} & {action_key(v) for v in allowed}
        if overlap:
            raise WafProfileError(
                f"action values mapped to both BLOCKED and ALLOWED: {sorted(overlap)}"
            )
        if (blocked or allowed) and self.action is None:
            raise WafProfileError("an action map needs an action property")

    @classmethod
    def from_profile(cls, profile: WafSourceProfile) -> WafFieldMapping:
        return cls(
            url=profile.url_property,
            method=profile.method_property,
            virtual_host=profile.virtual_host_property,
            action=profile.action_property,
            response_code=profile.response_code_property,
            rule_id=profile.rule_id_property,
            user_agent=profile.user_agent_property,
            blocked_actions=tuple((profile.action_value_map or {}).get("BLOCKED", ())),
            allowed_actions=tuple((profile.action_value_map or {}).get("ALLOWED", ())),
        )

    def property_for(self, field: WafLogicalField) -> str | None:
        return getattr(self, _COLUMN_BY_FIELD[field].removesuffix("_property"))

    def mapped(self) -> dict[WafLogicalField, str]:
        out: dict[WafLogicalField, str] = {}
        for field in WafLogicalField:
            name = self.property_for(field)
            if name is not None:
                out[field] = name
        return out

    def collection_fields(self) -> frozenset[WafLogicalField]:
        """Fields the collection query reads. A mapped virtual host is grouped
        on, so it must be as sound as the required fields."""
        if self.virtual_host is not None:
            return REQUIRED_FIELDS | {WafLogicalField.VIRTUAL_HOST}
        return REQUIRED_FIELDS

    def action_value_map(self) -> dict[str, list[str]]:
        if not (self.blocked_actions or self.allowed_actions):
            return {}
        return {"BLOCKED": list(self.blocked_actions), "ALLOWED": list(self.allowed_actions)}

    def action_outcome(self, raw: str) -> str | None:
        """BLOCKED, ALLOWED, or None for a value the map does not name."""
        key = action_key(raw)
        if key in {action_key(v) for v in self.blocked_actions}:
            return "BLOCKED"
        if key in {action_key(v) for v in self.allowed_actions}:
            return "ALLOWED"
        return None

    def apply_to(self, profile: WafSourceProfile) -> None:
        for field, column in _COLUMN_BY_FIELD.items():
            setattr(profile, column, self.property_for(field))
        profile.action_value_map = self.action_value_map()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {field.value: self.property_for(field) for field in WafLogicalField}
        out["ACTION_VALUE_MAP"] = self.action_value_map()
        return out


def collection_ineligibility(
    profile: WafSourceProfile, log_source: LogSource | None, settings: Settings
) -> str | None:
    """Why this source must not be collected now, or None when it may be.

    Every condition fails closed. In particular a profile whose mapping changed
    after its last validation does not run on the unvalidated mapping.
    """
    if not settings.waf_collection_enabled:
        return "WAF collection is disabled (WAF_COLLECTION_ENABLED=false)"
    if log_source is None:
        return "the profile's log source no longer exists"
    if not profile.enabled:
        return "the profile is disabled"
    if not log_source.monitoring_enabled:
        return "monitoring is disabled for the log source"
    if profile.validated_profile_version != profile.profile_version:
        if profile.validated_profile_version is None:
            return f"profile version {profile.profile_version} has never been validated"
        return (
            f"profile version {profile.profile_version} has not been validated "
            f"(last validated: {profile.validated_profile_version})"
        )
    if profile.validation_status != WafProfileValidationStatus.VALID:
        return f"profile validation is {profile.validation_status}"
    return None


@dataclass(frozen=True)
class BreakdownPlan:
    """Which optional breakdowns one source collects, and why the others do not."""

    collect: frozenset[WafBreakdown]
    #: Breakdown -> operator-readable reason it is not collected.
    skipped: Mapping[WafBreakdown, str]


def _validated_field(
    report: Mapping[str, Any], field: WafLogicalField
) -> Mapping[str, Any] | None:
    for entry in report.get("fields", ()):
        if entry.get("field") == field.value:
            return entry
    return None


def breakdown_plan(profile: WafSourceProfile, settings: Settings) -> BreakdownPlan:
    """Decide, failing closed, which breakdowns this source may collect.

    Every condition must hold for the profile's *current* version: a field is
    usable only when the last validation of this version found it AVAILABLE.
    The action breakdown additionally needs an authored map with at least one
    BLOCKED value, and every action value validation discovered must be mapped:
    a map that is already known to be stale would report unmapped traffic as
    never blocked. The source-IP breakdown reads QRadar's normalized `sourceip`,
    which every event carries, so it needs only the setting.
    """
    mapping = WafFieldMapping.from_profile(profile)
    report = profile.validation_report or {}
    current = (
        profile.validation_status == WafProfileValidationStatus.VALID
        and profile.validated_profile_version == profile.profile_version
    )
    collect: set[WafBreakdown] = set()
    skipped: dict[WafBreakdown, str] = {}

    def field_ready(field: WafLogicalField) -> str | None:
        if mapping.property_for(field) is None:
            return f"no {field.value} property is mapped"
        if not current:
            return "the profile's current version has not validated"
        entry = _validated_field(report, field)
        status = entry.get("status") if entry else None
        if status != WafFieldStatus.AVAILABLE:
            return f"{field.value} validated as {status or 'unknown'}, not AVAILABLE"
        return None

    if not settings.waf_outcome_collection_enabled:
        reason = "outcome collection is disabled (WAF_OUTCOME_COLLECTION_ENABLED=false)"
        skipped[WafBreakdown.ACTION] = reason
        skipped[WafBreakdown.RESPONSE_CODE] = reason
    else:
        action_reason = field_ready(WafLogicalField.ACTION)
        if action_reason is None and not mapping.blocked_actions:
            action_reason = "the profile's action map names no BLOCKED value"
        if action_reason is None:
            entry = _validated_field(report, WafLogicalField.ACTION) or {}
            unmapped = sorted(
                str(v.get("value"))
                for v in entry.get("discovered_values", ())
                if mapping.action_outcome(str(v.get("value", ""))) is None
            )
            if unmapped:
                action_reason = (
                    "validation discovered action values the map does not name: "
                    + ", ".join(unmapped[:10])
                )
        if action_reason is None:
            collect.add(WafBreakdown.ACTION)
        else:
            skipped[WafBreakdown.ACTION] = action_reason
        code_reason = field_ready(WafLogicalField.RESPONSE_CODE)
        if code_reason is None:
            collect.add(WafBreakdown.RESPONSE_CODE)
        else:
            skipped[WafBreakdown.RESPONSE_CODE] = code_reason

    if settings.waf_client_collection_enabled:
        collect.add(WafBreakdown.SOURCE_IP)
    else:
        skipped[WafBreakdown.SOURCE_IP] = (
            "client collection is disabled (WAF_CLIENT_COLLECTION_ENABLED=false)"
        )
    return BreakdownPlan(frozenset(collect), skipped)
