"""The QRadar custom-property catalog served to the mapping editor.

Names, types and flags only. No regex, payload, expression or format string
ever appears in a field, and `detail` is a fixed sentence, never upstream text.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PropertyCatalogItemOut(BaseModel):
    name: str
    property_type: str | None = None
    #: True: an enabled expression covers the source's type. False: none does.
    #: None: the source's type is unknown, so applicability cannot be judged.
    applies_to_source_type: bool | None = None
    #: False: the name cannot be used in AQL; shown but not selectable.
    name_allowed: bool


class PropertyCatalogOut(BaseModel):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    fetched_at: datetime | None = None
    #: False: the whole console's list, with no applicability judgement.
    filtered_by_source_type: bool
    properties: list[PropertyCatalogItemOut]
    detail: str | None = None
