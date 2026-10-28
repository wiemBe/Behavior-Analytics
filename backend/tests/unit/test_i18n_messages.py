"""Backend message registry validation, rendering, and wire values."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from app.i18n.messages import (
    REGISTRY,
    MessageRef,
    MessageRegistryError,
    MessageRenderError,
    MessageSpec,
    ParamKind,
    ParamSpec,
    _to_wire,
    render,
    validate_registry,
)
from app.schemas.messages import MessageParamValue, MessageValue


def _spec(
    code: str = "test.message",
    template: str = "value {value}",
    params: dict[str, ParamSpec] | None = None,
    sample: dict[str, object] | None = None,
) -> MessageSpec:
    return MessageSpec(
        code=code,
        template=template,
        params=params if params is not None else {"value": ParamSpec(ParamKind.TEXT)},
        sample=sample if sample is not None else {"value": "sample"},
    )


def test_every_registered_message_renders_its_sample() -> None:
    for spec in REGISTRY:
        assert render(MessageRef(spec.code, spec.sample))


def test_rejects_a_code_outside_the_dotted_pattern() -> None:
    with pytest.raises(MessageRegistryError, match="invalid message code"):
        validate_registry([_spec(code="UPPER")])


def test_rejects_duplicate_codes() -> None:
    with pytest.raises(MessageRegistryError, match="unique"):
        validate_registry([_spec(), _spec()])


def test_rejects_a_segment_prefix_collision() -> None:
    with pytest.raises(MessageRegistryError, match="segment prefix"):
        validate_registry(
            [
                _spec(code="test.message"),
                _spec(code="test.message.child"),
            ]
        )


def test_rejects_a_declared_param_missing_from_the_template() -> None:
    with pytest.raises(MessageRegistryError, match=r"missing=\['value'\]"):
        validate_registry([_spec(template="fixed")])


def test_rejects_an_undeclared_template_param() -> None:
    with pytest.raises(MessageRegistryError, match=r"undeclared=\['other'\]"):
        validate_registry([_spec(template="value {value} {other}")])


def test_rejects_an_enum_without_a_frontend_group() -> None:
    with pytest.raises(MessageRegistryError, match="needs a group"):
        validate_registry(
            [
                _spec(
                    params={"value": ParamSpec(ParamKind.ENUM)},
                    sample={"value": "PARTIAL"},
                )
            ]
        )


def test_rejects_a_ref_to_an_unknown_code() -> None:
    with pytest.raises(MessageRegistryError, match="unknown codes"):
        validate_registry(
            [
                _spec(
                    params={
                        "value": ParamSpec(ParamKind.REF, ref_codes=("missing.message",))
                    },
                    sample={"value": MessageRef("missing.message")},
                )
            ]
        )


def test_rejects_a_ref_cycle() -> None:
    a = _spec(
        code="test.a",
        params={"value": ParamSpec(ParamKind.REF, ref_codes=("test.b",))},
        sample={"value": MessageRef("test.b")},
    )
    b = _spec(
        code="test.b",
        params={"value": ParamSpec(ParamKind.REF, ref_codes=("test.a",))},
        sample={"value": MessageRef("test.a")},
    )
    with pytest.raises(MessageRegistryError, match="cycle"):
        validate_registry([a, b])


def test_rejects_an_unknown_formatting_style() -> None:
    with pytest.raises(MessageRegistryError, match="unsupported count style"):
        validate_registry(
            [
                _spec(
                    params={"value": ParamSpec(ParamKind.COUNT, style="surprising")},
                    sample={"value": 1},
                )
            ]
        )


def _all_kinds_registry() -> tuple[MessageSpec, ...]:
    child = MessageSpec("test.child", "child", {}, {})
    params = {
        "count": ParamSpec(ParamKind.COUNT),
        "number": ParamSpec(ParamKind.NUMBER),
        "percent": ParamSpec(ParamKind.PERCENT, precision=1),
        "points": ParamSpec(ParamKind.POINTS, precision=1),
        "duration": ParamSpec(ParamKind.DURATION),
        "timestamp": ParamSpec(ParamKind.TIMESTAMP),
        "enum": ParamSpec(ParamKind.ENUM, enum_group="anomalyState"),
        "list": ParamSpec(ParamKind.LIST),
        "ref": ParamSpec(ParamKind.REF, ref_codes=("test.child",)),
        "text": ParamSpec(ParamKind.TEXT),
    }
    sample: dict[str, object] = {
        "count": 1204,
        "number": 12.5,
        "percent": 0.318,
        "points": 0.297,
        "duration": 5400,
        "timestamp": datetime(2026, 10, 28, 9, 15, tzinfo=UTC),
        "enum": "PARTIAL",
        "list": ["alpha", "beta"],
        "ref": MessageRef("test.child"),
        "text": "verbatim",
    }
    template = " ".join(f"{name}={{{name}}}" for name in params)
    return child, MessageSpec("test.all", template, params, sample)


def test_wire_form_is_pinned_for_every_param_kind() -> None:
    registry = _all_kinds_registry()
    spec = registry[1]
    wire = _to_wire(MessageRef(spec.code, spec.sample), registry)
    assert wire == {
        "code": "test.all",
        "params": {
            "count": 1204,
            "number": 12.5,
            "percent": {"kind": "percent", "ratio": 0.318, "precision": 1},
            "points": {
                "kind": "points",
                "points": 29.7,
                "precision": 1,
                "signed": True,
            },
            "duration": {"kind": "duration", "seconds": 5400},
            "timestamp": "2026-10-28T09:15:00+00:00",
            "enum": "PARTIAL",
            "list": {"kind": "list", "items": ["alpha", "beta"]},
            "ref": {"code": "test.child", "params": {}},
            "text": "verbatim",
        },
    }
    assert MessageValue.model_validate(wire).model_dump() == wire


def test_wire_schema_rejects_an_invalid_tagged_value() -> None:
    adapter = TypeAdapter(MessageParamValue)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "duration", "seconds": -1})


def test_render_rejects_an_unknown_code() -> None:
    with pytest.raises(MessageRenderError, match="unknown message code"):
        render(MessageRef("missing.message"))


def test_render_rejects_a_missing_param() -> None:
    registry = (_spec(),)
    with pytest.raises(MessageRenderError, match=r"missing=\['value'\]"):
        render(MessageRef("test.message"), registry=registry)


def test_render_rejects_a_wrong_param_kind() -> None:
    registry = (
        _spec(
            params={"value": ParamSpec(ParamKind.COUNT)},
            sample={"value": 1},
        ),
    )
    with pytest.raises(MessageRenderError, match="integer count"):
        render(MessageRef("test.message", {"value": "one"}), registry=registry)


def test_pilot_english_is_pinned_byte_for_byte() -> None:
    assert render(MessageRef("explanation.dimension.unpopulated")) == (
        "field is not populated for this log source"
    )


def test_rejects_an_out_of_range_precision() -> None:
    with pytest.raises(MessageRegistryError, match="invalid precision"):
        validate_registry(
            [
                _spec(
                    params={"value": ParamSpec(ParamKind.PERCENT, precision=9)},
                    sample={"value": 0.5},
                )
            ]
        )


def test_rejects_a_template_that_formats_inside_the_placeholder() -> None:
    with pytest.raises(MessageRegistryError, match="bare placeholders"):
        validate_registry([_spec(template="value {value:.1f}")])


def test_rejects_a_sample_that_does_not_match_the_declaration() -> None:
    with pytest.raises(MessageRegistryError, match="sample params"):
        validate_registry([_spec(sample={})])


def test_render_rejects_a_ref_outside_the_codes_the_param_allows() -> None:
    other = MessageSpec("test.other", "other", {}, {})
    parent = MessageSpec(
        "test.parent",
        "{value}",
        {"value": ParamSpec(ParamKind.REF, ref_codes=("test.child",))},
        {"value": MessageRef("test.child")},
    )
    child = MessageSpec("test.child", "child", {}, {})
    registry = (child, other, parent)
    validate_registry(registry)
    assert render(MessageRef("test.parent", {"value": MessageRef("test.child")}), registry=registry)
    with pytest.raises(MessageRenderError, match="does not allow"):
        render(MessageRef("test.parent", {"value": MessageRef("test.other")}), registry=registry)


def test_render_rejects_an_undeclared_extra_param() -> None:
    with pytest.raises(MessageRenderError, match=r"extra=\['other'\]"):
        render(
            MessageRef("test.message", {"value": "x", "other": "y"}),
            registry=(_spec(),),
        )
