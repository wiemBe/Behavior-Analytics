"""API schemas for the geography view (D-10).

`share` is over country events only; the remainders sit beside it and are never mixed in.
A bucket that was not measured has no row, so coverage says how many were. A range with no
measured bucket at all carries no measurement of any kind.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.geo.fold import GeoDirection

UNRESOLVED_NOTE = (
    "UNRESOLVED counts events whose address is private, internal or unknown to GeoIP. "
    "The lookup returns the same empty result for all three, so they cannot be told apart."
)


class GeoStatus(StrEnum):
    #: No bucket in the range has been collected.
    NOT_COLLECTED = "NOT_COLLECTED"
    #: Buckets were collected, but no address in them resolved to a country.
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    #: Some buckets of the range are missing.
    PARTIAL = "PARTIAL"
    OK = "OK"


class GeoMeasurement(StrEnum):
    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"


class GeoCountryOut(BaseModel):
    country_code: str
    events: int
    #: This country's events over all country events in range, 0 to 1.
    share: float


class GeoRemaindersOut(BaseModel):
    UNRESOLVED: int = 0
    OVER_CAP: int = 0
    UNPARSEABLE: int = 0


class GeoCoverageOut(BaseModel):
    measured_buckets: int
    expected_buckets: int


class GeoCountriesOut(BaseModel):
    direction: GeoDirection
    since: datetime
    until: datetime
    countries: list[GeoCountryOut] = Field(default_factory=list)
    #: The remainder counts of the measured buckets. `None` while `status` is NOT_COLLECTED:
    #: a range that was never measured has no remainder to report, not a zero for each kind.
    remainders: GeoRemaindersOut | None
    #: LOWER_BOUND if any measured bucket in range is, EXACT if every one is. `None` while
    #: `status` is NOT_COLLECTED: nothing was measured, so nothing is exact either.
    measurement: GeoMeasurement | None
    coverage: GeoCoverageOut
    status: GeoStatus
    unresolved_note: str = UNRESOLVED_NOTE
