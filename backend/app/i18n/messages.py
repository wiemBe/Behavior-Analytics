"""Registry and English renderer for backend-authored analyst messages.

The API keeps the rendered English sentence and adds a stable code plus raw
parameters. English clients keep the sentence byte for byte; another locale
may render the code and parameters through its catalog.

Formatting styles are deliberately closed. Counts accept ``grouped`` (the
default) or ``plain`` (no grouping; it shapes the backend's English render only, the
frontend groups every count in the reader's locale); numbers accept ``general`` (the
default), ``grouped`` or ``fixed``; points accept ``signed`` (the default) or
``unsigned``; lists accept ``comma`` (the default) or ``and``. Percent precision,
fixed-number precision, and point precision are declared separately. Duration, timestamp, enum, ref,
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
    # --- MSG-2: rule statistics (Family 9) and detection coverage (Family 10) ----------------
    MessageSpec(
        code="rule_statistics.window.none",
        template=(
            "No rule-match window has been collected for this instance: collection is disabled "
            "or has not run yet, so nothing is measured."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.window.unsettled",
        template="No rule-match window in the requested span has settled yet.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.window.incomplete",
        template=(
            "Only {complete} of {expected} windows in the span are complete "
            "({missing} missing, {truncated} truncated, {failed} failed)."
            " Absence from an incomplete window proves nothing."
        ),
        params={
            "complete": ParamSpec(ParamKind.COUNT, style="plain"),
            "expected": ParamSpec(ParamKind.COUNT, style="plain"),
            "missing": ParamSpec(ParamKind.COUNT, style="plain"),
            "truncated": ParamSpec(ParamKind.COUNT, style="plain"),
            "failed": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"complete": 23, "expected": 24, "missing": 1, "truncated": 0, "failed": 0},
    ),
    MessageSpec(
        code="rule_statistics.top.no_complete_window",
        template="No complete window to measure.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.silent.excluded",
        template=(
            "Only EVENT rules and EVENT building blocks are judged: an event search sees every "
            "one of their matches. COMMON rules also match on flows, which are not searched, so "
            "their silence is unknown. FLOW and OFFENSE rules are not searched at all."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.cost.label",
        template=(
            "QRadar capacity benchmark: how fast the rule could be evaluated. "
            "Lower is costlier to evaluate. This is not a firing rate."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="rule_statistics.cost.unit",
        template="millions of events per second (QRadar's unit)",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.unmapped",
        template="No rule is mapped to this technique.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.all_excluded",
        template=(
            "Every rule mapped to this technique is disabled or discounted, "
            "so there is no active detection."
        ),
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.none_detecting",
        template="{degraded} of {enabled} enabled rules are not currently detecting.",
        params={
            "degraded": ParamSpec(ParamKind.COUNT, style="plain"),
            "enabled": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"degraded": 2, "enabled": 3},
    ),
    MessageSpec(
        code="coverage.rollup.not_established",
        template="Rule health has not been established for the mapped rules.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.rollup.some_degraded",
        template="{working} of {enabled} enabled rules are detecting; {degraded} are degraded.",
        params={
            "working": ParamSpec(ParamKind.COUNT, style="plain"),
            "enabled": ParamSpec(ParamKind.COUNT, style="plain"),
            "degraded": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"working": 1, "enabled": 3, "degraded": 2},
    ),
    MessageSpec(
        code="coverage.rollup.some_unestablished",
        template=(
            "{working} of {enabled} enabled rules are detecting; the rest are not yet established."
        ),
        params={
            "working": ParamSpec(ParamKind.COUNT, style="plain"),
            "enabled": ParamSpec(ParamKind.COUNT, style="plain"),
        },
        sample={"working": 2, "enabled": 3},
    ),
    MessageSpec(
        code="coverage.rollup.all_detecting",
        template="All {enabled} enabled rules mapped to this technique are detecting.",
        params={"enabled": ParamSpec(ParamKind.COUNT, style="plain")},
        sample={"enabled": 4},
    ),
    MessageSpec(
        code="coverage.evidence.below_floor",
        template=(
            "Mapping confidence {confidence} is below the {floor} floor; recorded "
            "but not counted toward coverage."
        ),
        params={
            "confidence": ParamSpec(ParamKind.NUMBER, style="fixed", precision=2),
            "floor": ParamSpec(ParamKind.NUMBER, style="fixed", precision=2),
        },
        sample={"confidence": 0.3, "floor": 0.5},
    ),
    MessageSpec(
        code="coverage.evidence.disabled",
        template="The rule is disabled, so it provides no active coverage.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.acceptable",
        template="The rule is enabled and its health is acceptable.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.dependency_degraded",
        template="The rule's required telemetry is unavailable, so it cannot fire.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.never_observed",
        template="The rule has never been observed firing.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.inactive",
        template="The rule has not fired within the inactivity window.",
        params={},
        sample={},
    ),
    MessageSpec(
        code="coverage.evidence.not_established",
        template="The rule's health has not been established yet.",
        params={},
        sample={},
    ),
    # --- end MSG-2 ---------------------------------------------------------------------------
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
        if not isinstance(value, MessageRef):
            raise MessageRenderError(f"{name} must be a message reference")
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
        assert isinstance(value, MessageRef)
        return _to_wire(value, registry)
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

