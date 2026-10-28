"""Wire models for coded backend-authored messages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
