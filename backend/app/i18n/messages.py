"""Registry and English renderer for backend-authored analyst messages.

The API keeps the rendered English sentence and adds a stable code plus raw
parameters. English clients keep the sentence byte for byte; another locale
may render the code and parameters through its catalog.

Formatting styles are deliberately closed. Counts accept ``grouped`` (the
default) or ``plain``; numbers accept ``general`` (the default), ``grouped`` or
``fixed``; points accept ``signed`` (the default) or ``unsigned``; lists accept
``comma`` (the default) or ``and``. Percent precision, fixed-number precision,
and point precision are declared separately. Duration, timestamp, enum, ref,
and text params have one spelling and therefore no alternate style.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from string import Formatter

from app.core.text import percent, points

CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class MessageRegistryError(ValueError):
    """The static message registry is internally inconsistent."""


class MessageRenderError(ValueError):
    """A message reference cannot be rendered against the registry."""


class ParamKind(StrEnum):
    COUNT = "count"
    NUMBER = "number"
    PERCENT = "percent"
    POINTS = "points"
    DURATION = "duration"
    TIMESTAMP = "timestamp"
    ENUM = "enum"
    LIST = "list"
    REF = "ref"
    TEXT = "text"


@dataclass(frozen=True)
class ParamSpec:
    """One template parameter and the information both renderers need."""

    kind: ParamKind
    enum_group: str | None = None
    ref_codes: tuple[str, ...] = ()
    style: str | None = None
    precision: int | None = None

    def to_artifact(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind.value}
        if self.enum_group is not None:
            out["enum_group"] = self.enum_group
        if self.ref_codes:
            out["ref_codes"] = list(self.ref_codes)
        if self.style is not None:
            out["style"] = self.style
        if self.precision is not None:
            out["precision"] = self.precision
        return out


@dataclass(frozen=True)
class MessageSpec:
    """One stable code, its legacy English template, and representative data."""

    code: str
    template: str
    params: Mapping[str, ParamSpec]
    sample: Mapping[str, object]


@dataclass(frozen=True)
class MessageRef:
    """A code and raw semantic values that can be rendered or sent on the wire."""

    code: str
    params: Mapping[str, object] = field(default_factory=dict)

    def to_wire(self) -> dict[str, object]:
        return _to_wire(self, REGISTRY)


REGISTRY: tuple[MessageSpec, ...] = (
    MessageSpec(
        code="explanation.dimension.unpopulated",
        template="field is not populated for this log source",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.population_not_measured",
        template="population was not measured",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.total_not_measured",
        template="the source's total event volume was not measured",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.no_events",
        template="the source produced no events in the window; nothing can be concluded",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.field.insufficient_coverage",
        template="populated on {populated} of events, below the required {required}",
        params={
            "populated": ParamSpec(ParamKind.PERCENT, precision=1),
            "required": ParamSpec(ParamKind.PERCENT),
        },
        sample={"populated": 0.318, "required": 0.9},
    ),
    MessageSpec(
        code="validation.waf.field.not_mapped",
        template="no QRadar property is mapped to this field",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.field.property_rejected",
        template="QRadar rejected a query naming this property; it does not exist as named",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.field.unpopulated",
        template="the property exists but no event in the window carried a value",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.field.implausible_ratio",
        template=(
            "{plausible} of sampled events carry a plausible {logical} value, "
            "below the required {required}"
        ),
        params={
            "plausible": ParamSpec(ParamKind.PERCENT, precision=1),
            "logical": ParamSpec(ParamKind.TEXT),
            "required": ParamSpec(ParamKind.PERCENT),
        },
        sample={"plausible": 0.125, "logical": "method", "required": 0.9},
    ),
    MessageSpec(
        code="validation.waf.field.implausible_no_values",
        template=(
            "no values of sampled events carry a plausible {logical} value, "
            "below the required {required}"
        ),
        params={
            "logical": ParamSpec(ParamKind.TEXT),
            "required": ParamSpec(ParamKind.PERCENT),
        },
        sample={"logical": "method", "required": 0.9},
    ),
    MessageSpec(
        code="validation.ips.field.not_mapped",
        template="no QRadar field is mapped to this logical field",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.ips.field.rejected",
        template="QRadar rejected the mapped field as named",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.ips.field.unpopulated",
        template="the field exists but no event in the window carried a usable value",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.lookup_failed",
        template="the log source lookup failed ({error})",
        params={"error": ParamSpec(ParamKind.TEXT)},
        sample={"error": "ProviderUnavailableError: timeout"},
    ),
    MessageSpec(
        code="validation.profile.source_missing",
        template="the log source does not exist in QRadar",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.total_not_measured",
        template="total event volume was not measured ({error})",
        params={"error": ParamSpec(ParamKind.TEXT)},
        sample={"error": "ProviderUnavailableError: timeout"},
    ),
    MessageSpec(
        code="validation.ips.profile.truncated",
        template="the bounded validation probe was truncated",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.field_reason",
        template="{field}: {status} ({detail})",
        params={
            "field": ParamSpec(ParamKind.TEXT),
            "status": ParamSpec(ParamKind.TEXT),
            "detail": ParamSpec(
                ParamKind.REF,
                ref_codes=(
                    "validation.field.population_not_measured",
                    "validation.field.total_not_measured",
                    "validation.field.no_events",
                    "validation.field.insufficient_coverage",
                    "validation.waf.field.not_mapped",
                    "validation.waf.field.property_rejected",
                    "validation.waf.field.unpopulated",
                    "validation.waf.field.implausible_ratio",
                    "validation.waf.field.implausible_no_values",
                    "validation.ips.field.not_mapped",
                    "validation.ips.field.rejected",
                    "validation.ips.field.unpopulated",
                ),
            ),
        },
        sample={
            "field": "URL",
            "status": "INSUFFICIENT_COVERAGE",
            "detail": MessageRef(
                "validation.field.insufficient_coverage",
                {"populated": 0.318, "required": 0.9},
            ),
        },
    ),
    MessageSpec(
        code="validation.waf.ineligible.collection_disabled",
        template="WAF collection is disabled (WAF_COLLECTION_ENABLED=false)",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.ineligible.source_missing",
        template="the profile's log source no longer exists",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.ineligible.disabled",
        template="the profile is disabled",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.waf.ineligible.monitoring_disabled",
        template="monitoring is disabled for the log source",
        params={},
        sample={},
    ),
    MessageSpec(
        code="validation.profile.ineligible.version_never_validated",
        template="profile version {profile_version} has never been validated",
        params={"profile_version": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"profile_version": 12000},
    ),
    MessageSpec(
        code="validation.profile.ineligible.version_not_validated",
        template=(
            "profile version {profile_version} has not been validated "
            "(last validated: {validated_profile_version})"
        ),
        params={
            "profile_version": ParamSpec(ParamKind.COUNT, style="plain"),
            "validated_profile_version": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"profile_version": 12000, "validated_profile_version": 9999},
    ),
    MessageSpec(
        code="validation.profile.ineligible.status",
        template="profile validation is {validation_status}",
        params={
            "validation_status": ParamSpec(
                ParamKind.ENUM,
                enum_group="profileValidationStatus",
            )
        },
        sample={"validation_status": "INVALID"},
    ),
)


_STYLES: dict[ParamKind, set[str | None]] = {
    ParamKind.COUNT: {None, "grouped", "plain"},
    ParamKind.NUMBER: {None, "general", "grouped", "fixed"},
    ParamKind.PERCENT: {None},
    ParamKind.POINTS: {None, "signed", "unsigned"},
    ParamKind.DURATION: {None},
    ParamKind.TIMESTAMP: {None},
    ParamKind.ENUM: {None},
    ParamKind.LIST: {None, "comma", "and"},
    ParamKind.REF: {None},
    ParamKind.TEXT: {None},
}


def _template_fields(template: str) -> set[str]:
    fields: set[str] = set()
    try:
        parsed = Formatter().parse(template)
        for _literal, name, format_spec, conversion in parsed:
            if name is None:
                continue
            if not name or "." in name or "[" in name or format_spec or conversion:
                raise MessageRegistryError(
                    "templates use bare placeholders; formatting belongs in ParamSpec"
                )
            fields.add(name)
    except ValueError as exc:
        raise MessageRegistryError(f"invalid Python format template: {exc}") from exc
    return fields


def validate_registry(registry: Sequence[MessageSpec]) -> None:
    """Reject a registry that cannot be represented or rendered unambiguously."""
    codes = [spec.code for spec in registry]
    if len(codes) != len(set(codes)):
        raise MessageRegistryError("message codes must be unique")
    for code in codes:
        if CODE_PATTERN.fullmatch(code) is None:
            raise MessageRegistryError(f"invalid message code: {code}")

    segment_codes = [(code, tuple(code.split("."))) for code in codes]
    for code, segments in segment_codes:
        for other, other_segments in segment_codes:
            if (
                code != other
                and len(segments) < len(other_segments)
                and other_segments[: len(segments)] == segments
            ):
                raise MessageRegistryError(
                    f"message code {code} is a segment prefix of {other}"
                )

    known = set(codes)
    refs: dict[str, set[str]] = {code: set() for code in codes}
    for spec in registry:
        declared = set(spec.params)
        present = _template_fields(spec.template)
        if declared != present:
            missing = sorted(declared - present)
            undeclared = sorted(present - declared)
            raise MessageRegistryError(
                f"template params for {spec.code} differ: "
                f"missing={missing}, undeclared={undeclared}"
            )
        if set(spec.sample) != declared:
            raise MessageRegistryError(
                f"sample params for {spec.code} do not match its declaration"
            )
        for name, param in spec.params.items():
            if not isinstance(param.kind, ParamKind):
                raise MessageRegistryError(f"{spec.code}.{name} has an invalid param kind")
            if param.style not in _STYLES[param.kind]:
                raise MessageRegistryError(
                    f"{spec.code}.{name} has unsupported {param.kind.value} style {param.style!r}"
                )
            if param.precision is not None and (
                isinstance(param.precision, bool) or not 0 <= param.precision <= 6
            ):
                raise MessageRegistryError(f"{spec.code}.{name} has invalid precision")
            if param.kind is ParamKind.ENUM:
                if not param.enum_group:
                    raise MessageRegistryError(f"enum param {spec.code}.{name} needs a group")
            elif param.enum_group is not None:
                raise MessageRegistryError(
                    f"only enum params may name an enum group: {spec.code}.{name}"
                )
            if param.kind is ParamKind.REF:
                if not param.ref_codes:
                    raise MessageRegistryError(f"ref param {spec.code}.{name} needs allowed codes")
                unknown = set(param.ref_codes) - known
                if unknown:
                    raise MessageRegistryError(
                        f"ref param {spec.code}.{name} names unknown codes: {sorted(unknown)}"
                    )
                refs[spec.code].update(param.ref_codes)
            elif param.ref_codes:
                raise MessageRegistryError(f"only ref params may name codes: {spec.code}.{name}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(code: str) -> None:
        if code in visiting:
            raise MessageRegistryError(f"message refs contain a cycle at {code}")
        if code in visited:
            return
        visiting.add(code)
        for target in refs[code]:
            visit(target)
        visiting.remove(code)
        visited.add(code)

    for code in codes:
        visit(code)


def _registry_map(registry: Sequence[MessageSpec]) -> dict[str, MessageSpec]:
    return {spec.code: spec for spec in registry}


def _numeric(value: object, *, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MessageRenderError(f"{name} must be a number")
    if not math.isfinite(value):
        raise MessageRenderError(f"{name} must be finite")
    return value


def _duration(value: object, *, name: str) -> str:
    seconds = _numeric(value, name=name)
    if seconds < 0:
        raise MessageRenderError(f"{name} must not be negative")
    total = round(seconds)
    if total == 0:
        return "0 s"
    parts: list[str] = []
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds_left = divmod(remainder, 60)
    for amount, unit in ((days, "d"), (hours, "h"), (minutes, "min"), (seconds_left, "s")):
        if amount:
            parts.append(f"{amount} {unit}")
    return " ".join(parts)


def _timestamp(value: object, *, name: str) -> str:
    instant: datetime
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, str):
        try:
            instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MessageRenderError(f"{name} must be an ISO-8601 timestamp") from exc
    else:
        raise MessageRenderError(f"{name} must be an ISO-8601 timestamp")
    if instant.tzinfo is None:
        raise MessageRenderError(f"{name} must include a timezone")
    instant = instant.astimezone(UTC)
    return instant.strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_param(
    name: str,
    spec: ParamSpec,
    value: object,
    registry: Sequence[MessageSpec],
) -> str:
    if spec.kind is ParamKind.COUNT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise MessageRenderError(f"{name} must be an integer count")
        return str(value) if spec.style == "plain" else f"{value:,}"
    if spec.kind is ParamKind.NUMBER:
        number = _numeric(value, name=name)
        if spec.style == "fixed":
            return f"{number:.{spec.precision if spec.precision is not None else 1}f}"
        if spec.style == "grouped":
            precision = spec.precision if spec.precision is not None else 0
            return f"{number:,.{precision}f}"
        return f"{number:g}"
    if spec.kind is ParamKind.PERCENT:
        number = _numeric(value, name=name)
        return percent(number, precision=spec.precision or 0)
    if spec.kind is ParamKind.POINTS:
        number = _numeric(value, name=name)
        return points(
            number,
            precision=spec.precision if spec.precision is not None else 1,
            signed=spec.style != "unsigned",
        )
    if spec.kind is ParamKind.DURATION:
        return _duration(value, name=name)
    if spec.kind is ParamKind.TIMESTAMP:
        return _timestamp(value, name=name)
    if spec.kind in (ParamKind.ENUM, ParamKind.TEXT):
        if not isinstance(value, str):
            raise MessageRenderError(f"{name} must be text")
        return value
    if spec.kind is ParamKind.LIST:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise MessageRenderError(f"{name} must be a list")
        rendered = [
            render(item, registry=registry) if isinstance(item, MessageRef) else str(item)
            for item in value
        ]
        if spec.style == "and" and len(rendered) > 1:
            return f"{', '.join(rendered[:-1])} and {rendered[-1]}"
        return ", ".join(rendered)
    if spec.kind is ParamKind.REF:
        if isinstance(value, str):
            return value
        if not isinstance(value, MessageRef):
            raise MessageRenderError(f"{name} must be a message reference or text")
        if value.code not in spec.ref_codes:
            raise MessageRenderError(f"{name} does not allow ref code {value.code}")
        return render(value, registry=registry)
    raise MessageRenderError(f"{name} has unsupported kind {spec.kind}")


def render(
    ref: MessageRef,
    *,
    registry: Sequence[MessageSpec] = REGISTRY,
) -> str:
    """Render a reference in the exact legacy English spelling."""
    spec = _registry_map(registry).get(ref.code)
    if spec is None:
        raise MessageRenderError(f"unknown message code: {ref.code}")
    expected = set(spec.params)
    actual = set(ref.params)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise MessageRenderError(f"params for {ref.code} differ: missing={missing}, extra={extra}")
    values = {
        name: _format_param(name, param, ref.params[name], registry)
        for name, param in spec.params.items()
    }
    return spec.template.format(**values)


def _wire_param(
    name: str,
    spec: ParamSpec,
    value: object,
    registry: Sequence[MessageSpec],
) -> object:
    # Validate through the same formatter first; wire values must never be more
    # permissive than the English sentence stored beside them.
    _format_param(name, spec, value, registry)
    if spec.kind is ParamKind.DURATION:
        return {"kind": "duration", "seconds": value}
    if spec.kind is ParamKind.PERCENT:
        return {
            "kind": "percent",
            "ratio": value,
            "precision": spec.precision or 0,
        }
    if spec.kind is ParamKind.POINTS:
        number = _numeric(value, name=name)
        return {
            "kind": "points",
            "points": number * 100,
            "precision": spec.precision if spec.precision is not None else 1,
            "signed": spec.style != "unsigned",
        }
    if spec.kind is ParamKind.LIST:
        assert isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        return {
            "kind": "list",
            "items": [
                _to_wire(item, registry) if isinstance(item, MessageRef) else item
                for item in value
            ],
        }
    if spec.kind is ParamKind.REF:
        return _to_wire(value, registry) if isinstance(value, MessageRef) else value
    if spec.kind is ParamKind.TIMESTAMP and isinstance(value, datetime):
        return value.isoformat()
    return value


def _to_wire(ref: MessageRef, registry: Sequence[MessageSpec]) -> dict[str, object]:
    specs = _registry_map(registry)
    spec = specs.get(ref.code)
    if spec is None:
        raise MessageRenderError(f"unknown message code: {ref.code}")
    # This pins missing/extra and kind errors before building the payload.
    render(ref, registry=registry)
    return {
        "code": ref.code,
        "params": {
            name: _wire_param(name, param, ref.params[name], registry)
            for name, param in spec.params.items()
        },
    }


def sample_english_params(spec: MessageSpec) -> dict[str, str]:
    """The already-English parameter strings used by the ICU artifact check."""
    return {
        name: _format_param(name, param, spec.sample[name], REGISTRY)
        for name, param in spec.params.items()
    }


validate_registry(REGISTRY)

