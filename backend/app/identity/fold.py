"""Fold one tick's three identity searches into bounded per-bucket facts.

Input is the rows of the counts search, the rare-name search and the volume-name
search (design §3), plus the (source, event ID, bucket) keys the tick asked
about. Output is the count, the measurement, and the top-N username list for
each key. N is 20, ordered by count descending then username ascending.

Rare event IDs keep a username from one event. 4625 and 4740 keep one only at
three events in the bucket. Names past N, names below that floor, and anything
that is not a plain username are discarded here. A username longer than 255
characters is cut and flagged. Nothing else text-shaped leaves this module.

A search that did not run is NOT_COLLECTED, never a zero. A truncated counts
search writes no healthy bucket. A truncated name search sets admission_truncated.
No database, no clock, no I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models.enums import (
    IDENTITY_CATALOG_VERSION,
    IDENTITY_EVENT_CATALOG,
    IDENTITY_RARE_NAME_FLOOR,
    IDENTITY_TOP_N,
    IDENTITY_USERNAME_MAX_LENGTH,
    IDENTITY_VOLUME_NAME_FLOOR,
    IdentityEventKind,
    IdentityMeasurement,
)

_Key = tuple[UUID, int, datetime]
_CATALOG = dict(IDENTITY_EVENT_CATALOG)
_VOLUME_IDS = frozenset(
    event_id for event_id, kind in IDENTITY_EVENT_CATALOG if kind is IdentityEventKind.VOLUME
)
_RARE_IDS = frozenset(_CATALOG) - _VOLUME_IDS


@dataclass(frozen=True)
class SeriesKey:
    """One series the tick asked about. An ID outside the catalog is ignored."""

    log_source_id: UUID
    event_id: int
    bucket_start: datetime


@dataclass(frozen=True)
class CountRow:
    """One group of the counts search: no username."""

    log_source_id: UUID
    event_id: int
    bucket_start: datetime
    count: int


@dataclass(frozen=True)
class NameRow:
    """One group of a name search. `username` is raw; the fold decides what is stored."""

    log_source_id: UUID
    event_id: int
    bucket_start: datetime
    username: object
    count: int


@dataclass(frozen=True)
class CountSearch:
    """The counts search for the tick. `ran` false is NOT_COLLECTED, whatever `rows` holds."""

    ran: bool
    truncated: bool
    rows: tuple[CountRow, ...] = ()


@dataclass(frozen=True)
class NameSearch:
    """A rare-name or volume-name search. `truncated` sets admission_truncated."""

    ran: bool
    truncated: bool
    rows: tuple[NameRow, ...] = ()


@dataclass(frozen=True)
class StoredUsername:
    """One username the allow-list lets out of the fold."""

    rank: int
    username: str
    count: int
    truncated: bool


@dataclass(frozen=True)
class IdentitySeries:
    """One (source, catalog event ID, bucket)."""

    log_source_id: UUID
    event_id: int
    bucket_start: datetime
    catalog_version: int
    count: int | None
    measurement: IdentityMeasurement
    names_measurement: IdentityMeasurement
    admission_truncated: bool
    usernames: tuple[StoredUsername, ...]


@dataclass(frozen=True)
class IdentityBucketFact:
    """The counts search rolled up to one source and bucket, plus its series."""

    log_source_id: UUID
    bucket_start: datetime
    catalog_version: int
    measurement: IdentityMeasurement
    population: int | None
    admission_truncated: bool
    series: tuple[IdentitySeries, ...]


@dataclass(frozen=True)
class IdentityFold:
    """Every bucket of one tick. `catalog_version` is the catalog the fold used."""

    catalog_version: int
    buckets: tuple[IdentityBucketFact, ...]

    def get(self, log_source_id: UUID, event_id: int, bucket_start: datetime) -> IdentitySeries:
        """The series for one key. Raises KeyError when the tick did not ask about it."""
        for bucket in self.buckets:
            if bucket.log_source_id != log_source_id or bucket.bucket_start != bucket_start:
                continue
            for series in bucket.series:
                if series.event_id == event_id:
                    return series
        raise KeyError((log_source_id, event_id, bucket_start))

    def bucket(self, log_source_id: UUID, bucket_start: datetime) -> IdentityBucketFact:
        for item in self.buckets:
            if item.log_source_id == log_source_id and item.bucket_start == bucket_start:
                return item
        raise KeyError((log_source_id, bucket_start))


@dataclass
class _NameTotal:
    username: str
    count: int
    truncated: bool


def _count_value(value: object) -> int | None:
    """A non-negative event count, or None when the cell is not a count."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _clean_username(raw: object) -> tuple[str, bool] | None:
    """`(stored, was_cut)`, or None when `raw` is not a plain username.

    Empty and whitespace-only text is not a username. A NUL cannot be stored,
    so the name stays in the fold. Longer than 255 characters is cut, and the
    flag says so. The stored text is QRadar's string, not a normalized form.
    """
    if not isinstance(raw, str) or "\x00" in raw or raw.strip() == "":
        return None
    cut = len(raw) > IDENTITY_USERNAME_MAX_LENGTH
    stored = raw[:IDENTITY_USERNAME_MAX_LENGTH] if cut else raw
    if stored.strip() == "":
        return None
    return stored, cut


def _key(log_source_id: UUID, event_id: int, bucket_start: datetime) -> _Key:
    return (log_source_id, event_id, bucket_start)


def _sum_counts(rows: Sequence[CountRow]) -> dict[_Key, int]:
    totals: dict[_Key, int] = {}
    for row in rows:
        if row.event_id not in _CATALOG:
            continue
        count = _count_value(row.count)
        if count is None:
            continue
        token = _key(row.log_source_id, row.event_id, row.bucket_start)
        totals[token] = totals.get(token, 0) + count
    return totals


def _sum_names(
    rows: Sequence[NameRow], allowed: frozenset[int]
) -> dict[_Key, dict[str, _NameTotal]]:
    """Names summed by the stored text. A name the cleaner refuses is absent."""
    totals: dict[_Key, dict[str, _NameTotal]] = {}
    for row in rows:
        if row.event_id not in allowed:
            continue
        count = _count_value(row.count)
        cleaned = _clean_username(row.username)
        if count is None or cleaned is None:
            continue
        stored, cut = cleaned
        token = _key(row.log_source_id, row.event_id, row.bucket_start)
        bucket = totals.setdefault(token, {})
        current = bucket.get(stored)
        if current is None:
            bucket[stored] = _NameTotal(stored, count, cut)
        else:
            current.count += count
            current.truncated = current.truncated or cut
    return totals


def _top_names(raw: dict[str, _NameTotal], floor: int) -> tuple[StoredUsername, ...]:
    """Count descending, username ascending, then the floor, then N."""
    kept = [item for item in raw.values() if item.count >= floor]
    kept.sort(key=lambda item: (-item.count, item.username))
    return tuple(
        StoredUsername(
            rank=index,
            username=item.username,
            count=item.count,
            truncated=item.truncated,
        )
        for index, item in enumerate(kept[:IDENTITY_TOP_N], start=1)
    )


def _series_count(
    token: _Key, counts: CountSearch, observed: dict[_Key, int]
) -> tuple[IdentityMeasurement, int | None]:
    """The count measurement. A missing row of a whole search is a real zero."""
    if not counts.ran:
        return IdentityMeasurement.NOT_COLLECTED, None
    if counts.truncated:
        # Absent under a cut is unknown, not zero. Present is the observed floor.
        return IdentityMeasurement.TRUNCATED, observed.get(token)
    if token in observed:
        return IdentityMeasurement.EXACT, observed[token]
    return IdentityMeasurement.EXACT, 0


def fold_tick(
    scope: Sequence[SeriesKey],
    counts: CountSearch,
    rare_names: NameSearch,
    volume_names: NameSearch,
) -> IdentityFold:
    """Fold one tick. Keys outside the catalog produce no output."""
    ordered: list[SeriesKey] = []
    seen: set[_Key] = set()
    for key in scope:
        if key.event_id not in _CATALOG:
            continue
        token = _key(key.log_source_id, key.event_id, key.bucket_start)
        if token in seen:
            continue
        seen.add(token)
        ordered.append(key)

    observed_counts = _sum_counts(counts.rows) if counts.ran else {}
    rare = _sum_names(rare_names.rows, _RARE_IDS) if rare_names.ran else {}
    volume = _sum_names(volume_names.rows, _VOLUME_IDS) if volume_names.ran else {}

    grouped: dict[tuple[UUID, datetime], list[IdentitySeries]] = defaultdict(list)
    for key in ordered:
        token = _key(key.log_source_id, key.event_id, key.bucket_start)
        kind = _CATALOG[key.event_id]
        measurement, count = _series_count(token, counts, observed_counts)
        if kind is IdentityEventKind.VOLUME:
            name_search, name_rows, floor = volume_names, volume, IDENTITY_VOLUME_NAME_FLOOR
        else:
            name_search, name_rows, floor = rare_names, rare, IDENTITY_RARE_NAME_FLOOR
        if name_search.ran:
            names_measurement = (
                IdentityMeasurement.TRUNCATED
                if name_search.truncated
                else IdentityMeasurement.EXACT
            )
            stored = _top_names(name_rows.get(token, {}), floor)
            admission_truncated = name_search.truncated
        else:
            names_measurement = IdentityMeasurement.NOT_COLLECTED
            stored = ()
            admission_truncated = False
        grouped[(key.log_source_id, key.bucket_start)].append(
            IdentitySeries(
                log_source_id=key.log_source_id,
                event_id=key.event_id,
                bucket_start=key.bucket_start,
                catalog_version=IDENTITY_CATALOG_VERSION,
                count=count,
                measurement=measurement,
                names_measurement=names_measurement,
                admission_truncated=admission_truncated,
                usernames=stored,
            )
        )

    buckets: list[IdentityBucketFact] = []
    for source_id, bucket_start in sorted(grouped, key=lambda item: (str(item[0]), item[1])):
        series = tuple(sorted(grouped[(source_id, bucket_start)], key=lambda item: item.event_id))
        measurement = series[0].measurement
        if measurement is IdentityMeasurement.NOT_COLLECTED:
            population = None
        else:
            known = [item.count for item in series if item.count is not None]
            population = sum(known) if known else None
        buckets.append(
            IdentityBucketFact(
                log_source_id=source_id,
                bucket_start=bucket_start,
                catalog_version=IDENTITY_CATALOG_VERSION,
                measurement=measurement,
                population=population,
                admission_truncated=any(item.admission_truncated for item in series),
                series=series,
            )
        )
    return IdentityFold(catalog_version=IDENTITY_CATALOG_VERSION, buckets=tuple(buckets))
