"""The pure fold of the geo search (D-10, G-1 decisions).

`GEO::LOOKUP(<field>, 'country')` returns a JSON string per row, for example
``{"country":{"confidence":null,"name":"United States","iso_code":"US","geo_id":6252001}}``,
and ``{}`` for a private, loopback or GeoIP-unknown address. The search groups by that
string as it is; this module parses it in Python and sums the rows per country code.

Guarantees:
  * No IP address is ever selected, so none is here. The key is device-controlled text:
    it is parsed inside a guard, never stored and never logged, only counted.
  * Only a parsed ``country.iso_code`` of two ASCII uppercase letters is a country.
    ``{}`` and an object with no ``country`` are UNRESOLVED; anything else is
    UNPARSEABLE (counted as a defect, never a country).
  * At most `max_countries` codes are kept per bucket, busiest first, ties by code.
    The rest are summed into OVER_CAP, so a bucket's events always add up.

No database, no clock and no provider call.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.collectors.bucketed_metrics import _integer_bucket_key, validate_bucketed_metric_window
from app.providers.base import ProviderError
from app.providers.dto import ProviderDTO
from app.providers.normalize import as_int

logger = logging.getLogger("app.geo.fold")

#: Stored on every row; bump it when the query or the fold changes meaning.
QUERY_VERSION = "geo-country-v1"
#: The cap on countries per (bucket, direction); the rest is OVER_CAP.
DEFAULT_MAX_COUNTRIES = 64
_ISO = re.compile(r"[A-Z]{2}")


class GeoDirection(StrEnum):
    """Which address the lookup is on: the source or the destination of the event."""

    SOURCE = "SOURCE"
    DESTINATION = "DESTINATION"


class GeoRemainder(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    OVER_CAP = "OVER_CAP"
    UNPARSEABLE = "UNPARSEABLE"


class GeoKeyedRow(ProviderDTO):
    """One keyed aggregate row: the geo key as QRadar returned it, a bucket, a volume.

    `key` is the raw lookup string. It is parsed by `fold_geo_bucket` and then dropped.
    """

    key: str | None = None
    bucket_start: datetime
    #: `SUM(eventcount)`.
    events: int


class GeoKeyedResult(ProviderDTO):
    """One geo search: the rows, whether they are whole, and what was searched."""

    direction: GeoDirection
    window_start: datetime
    window_end: datetime
    bucket_seconds: int
    #: Rows returned, at most `max_rows`, in the search's volume order.
    rows: tuple[GeoKeyedRow, ...] = ()
    #: More than `max_rows` rows existed: `rows` is the top, the tail is missing.
    truncated: bool = False
    #: The query text, for provenance. It holds no address and no caller text.
    query: str = ""


def parse_geo_rows(
    rows: Sequence[dict[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_rows: int,
) -> tuple[tuple[GeoKeyedRow, ...], bool]:
    """Keyed aggregate rows to validated `GeoKeyedRow`s, and whether they were cut.

    Shared by the REST provider and the mock. ``len(rows) > max_rows`` is truncation;
    the first `max_rows` rows are kept (the search is ordered by volume). A bucket key
    outside the window, a non-integer key and a malformed volume fail the whole search
    with a ProviderError that quotes nothing. The geo key is not judged here.
    """
    start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
    first_key = int(start.timestamp()) // bucket_seconds
    last_key = int(end.timestamp()) // bucket_seconds  # exclusive
    parsed: list[GeoKeyedRow] = []
    for row in rows:
        key = _integer_bucket_key(row.get("k"))
        if key is None:
            raise ProviderError("QRadar Ariel geo row had a non-integer bucket key")
        if not first_key <= key < last_key:
            raise ProviderError("QRadar Ariel geo row had a bucket key outside the window")
        events = as_int(row.get("events"))
        if events is None or events < 0:
            raise ProviderError("QRadar Ariel geo row had a malformed volume")
        raw = row.get("geo")
        parsed.append(
            GeoKeyedRow(
                key=raw if isinstance(raw, str) else None,
                bucket_start=datetime.fromtimestamp(key * bucket_seconds, tz=start.tzinfo),
                events=events,
            )
        )
    truncated = len(parsed) > max_rows
    return tuple(parsed[:max_rows]), truncated


@dataclass(frozen=True)
class GeoBucketFact:
    """What one measured bucket established, with no key left in it."""

    #: (country code, events), busiest first, at most `max_countries`.
    countries: tuple[tuple[str, int], ...]
    unresolved: int
    over_cap: int
    unparseable: int
    #: Distinct keys that were neither a country nor `{}`: a count, never the keys.
    unparseable_keys: int

    @property
    def events(self) -> int:
        return (
            sum(events for _code, events in self.countries)
            + self.unresolved
            + self.over_cap
            + self.unparseable
        )


def classify_key(key: str | None) -> str | GeoRemainder:
    """A country code, UNRESOLVED or UNPARSEABLE for one raw key. Never raises."""
    if key is None:
        return GeoRemainder.UNPARSEABLE
    if key == "{}":
        return GeoRemainder.UNRESOLVED
    try:
        value = json.loads(key)
    except (ValueError, RecursionError):
        return GeoRemainder.UNPARSEABLE
    if not isinstance(value, dict):
        return GeoRemainder.UNPARSEABLE
    if "country" not in value:
        return GeoRemainder.UNRESOLVED
    country = value["country"]
    code = country.get("iso_code") if isinstance(country, dict) else None
    if isinstance(code, str) and _ISO.fullmatch(code):
        return code
    return GeoRemainder.UNPARSEABLE


def fold_geo_bucket(
    rows: Sequence[GeoKeyedRow], *, max_countries: int = DEFAULT_MAX_COUNTRIES
) -> GeoBucketFact:
    """Sum the rows of one bucket per country code and cap the list.

    The caller passes the rows of a single bucket. A bucket with no row folds to an
    all-zero fact. Unparseable keys are logged by count only.
    """
    if max_countries < 1:
        raise ValueError("a bucket keeps at least one country")
    per_code: dict[str, int] = {}
    unresolved = 0
    unparseable = 0
    bad_keys: set[str] = set()
    for row in rows:
        kind = classify_key(row.key)
        if kind is GeoRemainder.UNRESOLVED:
            unresolved += row.events
        elif kind is GeoRemainder.UNPARSEABLE:
            unparseable += row.events
            bad_keys.add(row.key if row.key is not None else "")
        else:
            per_code[str(kind)] = per_code.get(str(kind), 0) + row.events
    ordered = sorted(per_code.items(), key=lambda item: (-item[1], item[0]))
    kept = tuple(ordered[:max_countries])
    over_cap = sum(events for _code, events in ordered[max_countries:])
    if bad_keys:
        logger.warning("geo fold: %d distinct unparseable key(s) in a bucket", len(bad_keys))
    return GeoBucketFact(
        countries=kept,
        unresolved=unresolved,
        over_cap=over_cap,
        unparseable=unparseable,
        unparseable_keys=len(bad_keys),
    )
