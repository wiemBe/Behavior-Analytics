"""Saved dashboards (UR.5.5): the layout JSON, its bounds, and the API contract.

The backend validates the structure of a layout, never its meaning. A widget `type` it does
not know is valid and is stored as is, so an older client does not destroy a newer client's
layout. `config` is opaque: the server never interprets it and never runs anything from it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.dashboard import DashboardVisibility

#: The layout versions this server reads. A client writing any other `v` is refused (422).
SUPPORTED_LAYOUT_VERSIONS: frozenset[int] = frozenset({1})

GRID_COLUMNS = 12
MAX_WIDGETS = 24
MAX_WIDGET_HEIGHT = 24
MAX_CONFIG_BYTES = 4 * 1024
MAX_LAYOUT_BYTES = 64 * 1024
MAX_DASHBOARDS_PER_OWNER = 20
MAX_NAME_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 2000

RangeKey = Literal["1h", "24h", "7d"]
Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_LENGTH)
]


def _serialized_size(value: Any) -> int:
    """Bytes of the compact UTF-8 JSON form: the size the limits are stated in."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class LayoutWidget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^w_[a-z0-9]{4,12}$")
    type: str = Field(pattern=r"^[a-z0-9_.]{1,64}$")
    x: StrictInt = Field(ge=0, le=GRID_COLUMNS - 1)
    y: StrictInt = Field(ge=0)
    w: StrictInt = Field(ge=1, le=GRID_COLUMNS)
    h: StrictInt = Field(ge=1, le=MAX_WIDGET_HEIGHT)
    range: RangeKey | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _inside_the_grid(self) -> LayoutWidget:
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError(f"x + w must be at most {GRID_COLUMNS}")
        return self

    @field_validator("config")
    @classmethod
    def _config_is_small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _serialized_size(value) > MAX_CONFIG_BYTES:
            raise ValueError(f"config is at most {MAX_CONFIG_BYTES} bytes serialized")
        return value


class DashboardLayout(BaseModel):
    """Layout JSON, version 1."""

    model_config = ConfigDict(extra="forbid")

    v: StrictInt
    range: RangeKey
    widgets: list[LayoutWidget] = Field(max_length=MAX_WIDGETS)

    @field_validator("v")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value not in SUPPORTED_LAYOUT_VERSIONS:
            raise ValueError(f"layout version {value} is not supported by this server")
        return value

    @model_validator(mode="after")
    def _unique_ids_and_size(self) -> DashboardLayout:
        ids = [widget.id for widget in self.widgets]
        if len(set(ids)) != len(ids):
            raise ValueError("widget ids must be unique within a layout")
        if _serialized_size(self.model_dump(mode="json")) > MAX_LAYOUT_BYTES:
            raise ValueError(f"the layout is at most {MAX_LAYOUT_BYTES} bytes serialized")
        return self


def upgrade_layout(layout: dict[str, Any]) -> dict[str, Any]:
    """Bring a stored layout to the current version. Applied on read.

    Pure. Version 1 is the only version, so today this is the identity; each future version
    adds one step here and one unit test. A version this server does not know raises, rather
    than being passed to a client that would misread it.
    """
    version = layout.get("v")
    if version not in SUPPORTED_LAYOUT_VERSIONS:
        raise ValueError(f"layout version {version!r} is not supported by this server")
    return layout


class DashboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    layout: DashboardLayout
    template_key: str | None = Field(default=None, min_length=1, max_length=32)
    template_version: int | None = Field(default=None, ge=1)


class DashboardReplace(BaseModel):
    """`PUT`: replaces name, description and layout; carries the revision it was based on."""

    model_config = ConfigDict(extra="forbid")

    name: Name
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    layout: DashboardLayout
    expected_revision: int = Field(ge=1)


class DashboardPatch(BaseModel):
    """`PATCH`: rename, describe, or change visibility. At least one field."""

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    visibility: DashboardVisibility | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> DashboardPatch:
        if not self.model_fields_set:
            raise ValueError("send at least one of name, description, visibility")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "visibility" in self.model_fields_set and self.visibility is None:
            raise ValueError("visibility cannot be null")
        return self


class DefaultDashboardIn(BaseModel):
    """`PUT /dashboards/default`. `dashboard_id` is required; null clears the default."""

    model_config = ConfigDict(extra="forbid")

    dashboard_id: uuid.UUID | None


class DashboardListItem(BaseModel):
    id: uuid.UUID
    name: str
    owner_subject: str
    visibility: DashboardVisibility
    template_key: str | None
    updated_at: datetime
    widget_count: int
    is_default: bool


class DashboardOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    owner_subject: str
    visibility: DashboardVisibility
    template_key: str | None
    template_version: int | None
    schema_version: int
    layout: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime
    updated_by: str | None
    is_default: bool


class PagedDashboards(BaseModel):
    items: list[DashboardListItem]
    total: int
    limit: int
    offset: int


class DefaultDashboardOut(BaseModel):
    dashboard_id: uuid.UUID | None
