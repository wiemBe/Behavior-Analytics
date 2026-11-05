"""The pivot block: text for the analyst to take to the QRadar console (SOC-2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PivotWindowOut(BaseModel):
    start: datetime
    stop: datetime


class PivotOut(BaseModel):
    """AQL text and console links. The product never runs the AQL."""

    kind: str
    aql: str
    #: The link "Open in QRadar" follows: the AQL search, or the offense page for an offense.
    #: None when the console address is unknown; `console_unavailable_reason` says why.
    console_url: str | None = None
    #: The Log Activity AQL search for `aql`. Equal to `console_url` except on an offense.
    aql_search_url: str | None = None
    console_unavailable_reason: str | None = None
    time_range: PivotWindowOut
    notes: list[str] = Field(default_factory=list)
