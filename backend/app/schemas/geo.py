"""API schemas for the geography view (D-10).

`share` is over country events only; the remainders sit beside it and are never mixed in.
A bucket that was not measured has no row, so coverage says how many were. A range with no
measured bucket at all carries no measurement of any kind.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.geo.fold import GeoDirection
from app.i18n.messages import MessageRef, render
from app.schemas.messages import MessageParamValue

UNRESOLVED_NOTE = (
    "UNRESOLVED counts events whose address is private, internal or unknown to GeoIP. "
    "The lookup returns the same empty result for all three, so they cannot be told apart."
)


class GeoCountrySource(StrEnum):
    PROPERTY = "PROPERTY"
    LOOKUP = "LOOKUP"


def country_source_fields(
    settings: Settings, direction: GeoDirection
) -> tuple[GeoCountrySource, str | None, str, str, dict[str, MessageParamValue]]:
    """The source, the configured property, and the translated unresolved note.

    Lookup mode reports no property name even when one is configured. An empty
    property name in property mode is null.
    """
    if settings.geo_country_source == "lookup":
        ref = MessageRef("geo.note.unresolved_lookup")
    elif settings.geo_country_source == "property":
        ref = MessageRef("geo.note.unresolved_property")
    else:
        raise ValueError("GEO_COUNTRY_SOURCE is property or lookup")
    wire = ref.to_wire()
    params = wire["params"]
    if not isinstance(params, dict):
        raise TypeError("unresolved note params")
    name: str | None = None
    if settings.geo_country_source == "property":
        configured = settings.geo_property_for(direction.value)
        name = configured or None
    source = GeoCountrySource(settings.geo_country_source.upper())
    return source, name, render(ref), ref.code, params


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


class GeoCollectedWayOut(BaseModel):
    """One way the buckets of the range were collected, and how many were."""

    country_source: GeoCountrySource
    #: The property read in `PROPERTY` mode, otherwise null.
    country_property: str | None = None
    buckets: int


class GeoCollectedWithOut(BaseModel):
    """How the plotted buckets were collected: the distinct ways present, no per-bucket list.

    `unknown_buckets` counts the buckets stored before 0.1.11, which did not record it.
    """

    ways: list[GeoCollectedWayOut] = Field(default_factory=list)
    unknown_buckets: int = 0


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
    #: The end of the newest measured bucket in range for this direction. `None` while
    #: `status` is NOT_COLLECTED: no bucket was measured, so no answer has an age.
    latest_bucket_end: datetime | None = None
    #: The current setting, not what the buckets used (that is `collected_with`).
    #: PROPERTY reads the configured property. LOOKUP is GEO::LOOKUP.
    country_source: GeoCountrySource
    #: The configured property for this direction in property mode, otherwise null.
    country_property: str | None = None
    #: How the measured buckets of the range were collected. `None` while `status` is
    #: NOT_COLLECTED: no bucket, so nothing to say about one.
    collected_with: GeoCollectedWithOut | None = None
    unresolved_note: str = UNRESOLVED_NOTE
    unresolved_note_code: str
    unresolved_note_params: dict[str, MessageParamValue] = Field(default_factory=dict)
