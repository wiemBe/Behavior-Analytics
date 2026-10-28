"""Wire models for coded backend-authored messages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

type JsonScalar = str | int | float | bool | None


class DurationParamValue(BaseModel):
    kind: Literal["duration"]
    seconds: float = Field(ge=0)


class PercentParamValue(BaseModel):
    kind: Literal["percent"]
    ratio: float
    precision: int = Field(default=0, ge=0, le=6)


class PointsParamValue(BaseModel):
    kind: Literal["points"]
    points: float
    precision: int = Field(default=1, ge=0, le=6)
    signed: bool = True


class ListParamValue(BaseModel):
    kind: Literal["list"]
    items: list[MessageParamValue]


class MessageValue(BaseModel):
    """A nested ``{code, params}`` value, also used for stored JSONB validation."""

    code: str
    params: dict[str, MessageParamValue] = Field(default_factory=dict)


type MessageParamValue = (
    JsonScalar
    | DurationParamValue
    | PercentParamValue
    | PointsParamValue
    | ListParamValue
    | MessageValue
)

ListParamValue.model_rebuild()
MessageValue.model_rebuild()


def stored_message(value: object) -> MessageValue | None:
    """A stored message reference with a valid wire shape, otherwise no code."""
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    params = value.get("params")
    if not isinstance(code, str) or not isinstance(params, dict):
        return None
    try:
        return MessageValue.model_validate({"code": code, "params": params})
    except ValidationError:
        return None


def stored_message_parts(
    code: object, params: object
) -> tuple[str | None, dict[str, MessageParamValue] | None]:
    """Validate flattened stored siblings without coercing malformed values."""
    message = stored_message({"code": code, "params": params})
    if message is None:
        return None, None
    return message.code, message.params


def stored_message_list(value: object) -> list[MessageValue | None] | None:
    """Validate a parallel stored code list, preserving malformed entries as null."""
    if not isinstance(value, list):
        return None
    return [stored_message(item) for item in value]


def stored_reasons(
    reasons: object, codes: object
) -> tuple[list[str], list[MessageValue | None] | None]:
    """Keep only the string reasons, with the code list filtered to the same positions."""
    if not isinstance(reasons, list):
        return [], stored_message_list(codes)
    kept = [index for index, item in enumerate(reasons) if isinstance(item, str)]
    parsed = stored_message_list(codes)
    if parsed is None:
        return [reasons[index] for index in kept], None
    return (
        [reasons[index] for index in kept],
        [parsed[index] if index < len(parsed) else None for index in kept],
    )


def validation_report_messages(report: dict[str, object]) -> dict[str, object]:
    """Copy a stored validation report with only well-shaped message metadata."""
    out = dict(report)
    out["reason_codes"] = stored_message_list(report.get("reason_codes"))
    raw_fields = report.get("fields")
    if not isinstance(raw_fields, list):
        return out
    fields: list[object] = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            fields.append(raw)
            continue
        field = dict(raw)
        code, params = stored_message_parts(
            raw.get("detail_code"), raw.get("detail_params")
        )
        field["detail_code"] = code
        field["detail_params"] = params
        fields.append(field)
    out["fields"] = fields
    return out
