"""The facts a property-watch collection hands to detection, and the rules that make them.

PW-2 folds search rows into `PwBucketFact`; PW-3 reads `PwBucketFact` and `PwReading`. This
module is the contract between them and holds the rules both must agree on (design §G.5,
§G.6, §G.8.0):

**The measured-zero rule.** An aggregate that matches nothing returns zero rows, not a row
holding 0. So zero rows, or an absent key, from a search that ended `COMPLETED` and was not
truncated (at most `cap` rows) is a measured zero for whatever the search asked about. Any
other end state -- failed, cancelled, timed out, still running, or truncated -- is
unmeasured: never 0 (AGENTS.md rule 8). A `LIMIT` cut is invisible on the console, so a
search carries `LIMIT cap + 1` and more than `cap` rows means truncated (PW-0 L8).

**Counts.** The console returns `SUM(eventcount)` and `COUNT(*)` as JSON floats (L6), so an
integer-valued float is accepted; a fractional, negative, NaN, infinite, boolean or
non-numeric count is refused, and a search that returns one is unmeasured.

**The unparsed remainder.** An unparsed property comes back as NULL in a `GROUP BY` (L5);
the fold counts NULL and the empty string alike as unparsed, never as a value.

**A reading** is one (value, bucket) observation, with the measurement decided by the bucket
and by how the value was searched, never by a detector (`reading_for`).

Pure. No I/O, no raw event: only counts and the bounded value lists the design allows.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.models.enums import (
    PwBucketMeasurement,
    PwBucketOrigin,
    PwPinnedMeasurement,
    WafMeasurement,
)

#: Joins the components of a combination watch's tuple key. A control character, which a
#: sanitized value text cannot contain.
TUPLE_SEPARATOR = "\x1f"


class PwFactError(ValueError):
    """A fact violated the collection contract: a bug or a hostile search result, not data."""


# --------------------------------------------------------------------- search outcomes
class PwSearchStatus(StrEnum):
    """How an Ariel search ended. Only `COMPLETED` can measure anything."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    RUNNING = "RUNNING"


def coerce_count(raw: object) -> int:
    """A count from a search row: an integer, or a float that is exactly one (L6).

    Raises `PwFactError` for a boolean, a string, `None`, NaN, infinity, a negative number
    or a fractional one. The caller treats that as a failed search, which is unmeasured.
    """
    if isinstance(raw, bool):
        raise PwFactError("a count cannot be a boolean")
    if isinstance(raw, int):
        count = raw
    elif isinstance(raw, float):
        if not math.isfinite(raw) or raw != int(raw):
            raise PwFactError("a count must be a whole number")
        count = int(raw)
    else:
        raise PwFactError("a count must be a number")
    if count < 0:
        raise PwFactError("a count cannot be negative")
    return count


def is_truncated(row_count: int, cap: int) -> bool:
    """More than `cap` rows means the `LIMIT cap + 1` search was cut. Exactly `cap` is whole."""
    if cap < 1:
        raise PwFactError("a row cap is at least 1")
    if row_count < 0:
        raise PwFactError("a row count cannot be negative")
    return row_count > cap


def is_unparsed(raw: object) -> bool:
    """Whether a grouped property value is the unparsed remainder rather than a value.

    NULL and the empty string are both unparsed (L5); so is text that is only whitespace,
    since the fold strips values and that leaves nothing. A non-text value is not a value
    either.
    """
    if raw is None:
        return True
    if not isinstance(raw, str):
        return True
    return not raw.strip()


def join_tuple_key(components: tuple[str, ...]) -> str:
    """The `value_key` of a combination watch's tuple."""
    if not components:
        raise PwFactError("a tuple key needs at least one component")
    if any(TUPLE_SEPARATOR in component for component in components):
        raise PwFactError("a component cannot contain the tuple separator")
    return TUPLE_SEPARATOR.join(components)


def split_tuple_key(value_key: str) -> tuple[str, ...]:
    return tuple(value_key.split(TUPLE_SEPARATOR))


# ---------------------------------------------------------------------------- counts
class PwCountState(StrEnum):
    EXACT = "EXACT"
    #: A real count that can only understate the truth.
    LOWER_BOUND = "LOWER_BOUND"
    #: Nothing was measured. `events` is `None`, never 0.
    UNMEASURED = "UNMEASURED"


@dataclass(frozen=True)
class PwCount:
    """What one search established about one key in one bucket."""

    events: int | None
    state: PwCountState

    def __post_init__(self) -> None:
        if self.state is PwCountState.UNMEASURED:
            if self.events is not None:
                raise PwFactError("an unmeasured count carries no number")
        elif self.events is None or self.events < 0:
            raise PwFactError("a measured count is a non-negative number")

    @property
    def measured_zero(self) -> bool:
        """Test-only. Production absence checks use ``PwReading.is_measured_zero``."""
        return self.state is PwCountState.EXACT and self.events == 0


UNMEASURED = PwCount(None, PwCountState.UNMEASURED)


def count_from_search(
    counts: Mapping[str, object],
    key: str,
    *,
    status: PwSearchStatus,
    row_count: int,
    cap: int,
) -> PwCount:
    """The count of `key` the search established, applying the measured-zero rule.

    A `COMPLETED`, whole search that does not mention `key` measured a zero. The same
    absence from any other end state is unmeasured. A truncated search keeps what it
    shows as lower bounds; what it does not show is unknown.
    """
    truncated = is_truncated(row_count, cap)
    if status is not PwSearchStatus.COMPLETED:
        return UNMEASURED
    if key in counts:
        events = coerce_count(counts[key])
        return PwCount(events, PwCountState.LOWER_BOUND if truncated else PwCountState.EXACT)
    if truncated:
        return UNMEASURED
    return PwCount(0, PwCountState.EXACT)


# ------------------------------------------------------------------------ bucket facts
@dataclass(frozen=True)
class PwValueFact:
    """One value or tuple the fold kept for a bucket. The text is bounded to 255."""

    value_key: str
    events: int
    components: tuple[str, ...] = ()
    #: The fold cut the text to the stored width.
    truncated: bool = False

    def __post_init__(self) -> None:
        if not self.value_key or len(self.value_key) > 255:
            raise PwFactError("a value key is 1 to 255 characters")
        if self.events < 0:
            raise PwFactError("a value cannot have negative events")


class PwRemainderBound(StrEnum):
    """Which way the unparsed remainder can be wrong."""

    #: The grouped search was whole: `total - parsed` is the remainder.
    EXACT = "EXACT"
    #: The grouped search was cut, so `parsed` under-counts and `total - parsed` over-states.
    UPPER_BOUND = "UPPER_BOUND"


@dataclass(frozen=True)
class PwCoverage:
    """`parsed / total`. `ratio` is `None` when it is unknown or undefined.

    Undefined, not 0, for a scope that sent nothing: a silent scope is not a parsing break.
    A `lower_bound` ratio can only understate coverage.
    """

    ratio: float | None
    lower_bound: bool

    def reaches(self, floor: float) -> bool:
        """Whether coverage is known to meet the floor. Unknown never does.

        A lower bound that reaches the floor proves true coverage does, so it counts.
        """
        return self.ratio is not None and self.ratio >= floor


@dataclass(frozen=True)
class PwBucketFact:
    """What collection established for one watch and bucket (the `pw_bucket` row).

    Every count that was not measured is `None`. `values` are the kept values only: a value
    the fold cut or that was not admitted is summed into `untracked_events` and its text is
    gone (AGENTS.md rule 3).
    """

    bucket_start: datetime
    bucket_seconds: int
    watch_version: int
    totals_complete: bool
    values_measurement: PwBucketMeasurement
    pinned_measurement: PwPinnedMeasurement
    origin: PwBucketOrigin = PwBucketOrigin.LIVE
    total_events: int | None = None
    parsed_events: int | None = None
    untracked_events: int | None = None
    values: tuple[PwValueFact, ...] = ()
    observed_values: int = 0
    tracked_values_observed: int = 0
    admitted_values: int = 0
    below_admission_threshold: int = 0
    rejected_over_quota: int = 0
    admission_truncated: bool = False
    scope_member_count: int | None = None
    scope_fingerprint: str | None = None
    scope_changed: bool = False
    in_maintenance: bool = False

    def __post_init__(self) -> None:
        if self.bucket_seconds <= 0:
            raise PwFactError("a bucket has a positive width")
        if self.watch_version < 1:
            raise PwFactError("a watch version starts at 1")
        if not self.totals_complete and self.total_events is not None:
            raise PwFactError("an incomplete totals search leaves the total unknown")
        for name in ("total_events", "parsed_events", "untracked_events"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise PwFactError(f"{name} cannot be negative")
        if (
            self.total_events is not None
            and self.parsed_events is not None
            and self.parsed_events > self.total_events
        ):
            raise PwFactError("parsed events cannot exceed total events")
        if self.values_measurement is PwBucketMeasurement.NOT_COLLECTED:
            if self.parsed_events is not None or self.untracked_events is not None:
                raise PwFactError("an uncollected bucket measured nothing")
        elif self.parsed_events is None:
            raise PwFactError("a collected bucket states its parsed events")
        if (
            self.untracked_events is not None
            and self.values_measurement is not PwBucketMeasurement.EXACT
        ):
            raise PwFactError("untracked events are known only from a whole grouped search")
        if self.parsed_events is not None and (
            sum(value.events for value in self.values) + (self.untracked_events or 0)
            > self.parsed_events
        ):
            raise PwFactError("the kept values cannot exceed the parsed events")

    @property
    def unparsed_events(self) -> int | None:
        """`total - parsed`, or `None` when either side is unknown. Never a value row."""
        if self.total_events is None or self.parsed_events is None:
            return None
        return self.total_events - self.parsed_events

    @property
    def unparsed_bound(self) -> PwRemainderBound | None:
        if self.unparsed_events is None:
            return None
        if self.values_measurement is PwBucketMeasurement.EXACT:
            return PwRemainderBound.EXACT
        return PwRemainderBound.UPPER_BOUND

    @property
    def coverage(self) -> PwCoverage:
        lower_bound = self.values_measurement is PwBucketMeasurement.LOWER_BOUND
        if not self.total_events or self.parsed_events is None:
            return PwCoverage(None, lower_bound)
        return PwCoverage(self.parsed_events / self.total_events, lower_bound)

    @property
    def property_not_parsed(self) -> bool:
        """The property parsed on no event although events arrived (`NOT_APPLICABLE`).

        A scope that sent nothing is not this: it needs `total_events > 0`.
        """
        return (
            self.values_measurement is PwBucketMeasurement.EXACT
            and bool(self.total_events)
            and self.parsed_events == 0
        )


# -------------------------------------------------------------------------- readings
class PwReadingKind(StrEnum):
    #: The value has a metric row.
    COUNT = "COUNT"
    #: No row, the value was tracked, and the search that would have listed it was whole.
    MEASURED_ZERO = "MEASURED_ZERO"
    #: No row, the value was tracked, and the measurement cannot say it was absent.
    UNKNOWN = "UNKNOWN"
    #: The value was not tracked at this bucket: not an observation at all.
    NOT_AN_OBSERVATION = "NOT_AN_OBSERVATION"
    #: No bucket row, or one that measured nothing: collection failed, never ran, the
    #: scope was stale or the budget was spent. Never zero.
    NO_BUCKET = "NO_BUCKET"


@dataclass(frozen=True)
class PwReading:
    kind: PwReadingKind
    events: int | None
    measurement: WafMeasurement | None

    @property
    def is_measured_zero(self) -> bool:
        return self.kind is PwReadingKind.MEASURED_ZERO


_NO_BUCKET = PwReading(PwReadingKind.NO_BUCKET, None, None)
_NOT_AN_OBSERVATION = PwReading(PwReadingKind.NOT_AN_OBSERVATION, None, None)


def _waf(measurement: PwBucketMeasurement | PwPinnedMeasurement) -> WafMeasurement | None:
    if measurement.value == WafMeasurement.EXACT.value:
        return WafMeasurement.EXACT
    if measurement.value == WafMeasurement.LOWER_BOUND.value:
        return WafMeasurement.LOWER_BOUND
    return None


def value_measurement(bucket: PwBucketFact, *, pinned: bool) -> WafMeasurement | None:
    """What the bucket says about a value's count: `EXACT`, `LOWER_BOUND` or unmeasured.

    A pinned value is `EXACT` whenever the small pinned search was whole, even when the
    open-ended search was not (design §G.8.0). Otherwise the grouped search decides.
    """
    if pinned and bucket.pinned_measurement is PwPinnedMeasurement.EXACT:
        return WafMeasurement.EXACT
    return _waf(bucket.values_measurement)


def reading_for(
    bucket: PwBucketFact | None,
    *,
    pinned: bool,
    tracked_at_bucket: bool,
    metric_events: int | None,
) -> PwReading:
    """One (value, bucket) reading (design §G.8.0).

    `metric_events` is the value's `pw_value_metric.events` for the bucket, `None` when it
    has no row. `tracked_at_bucket` is the tracking window test (`tracked_since` <= bucket,
    and the bucket is not past `tracked_until`).
    """
    if metric_events is not None and metric_events < 0:
        raise PwFactError("a metric row cannot hold negative events")
    if bucket is None:
        return _NO_BUCKET
    measurement = value_measurement(bucket, pinned=pinned)
    if metric_events is not None:
        if measurement is None:
            # A row inside a bucket that measured nothing contradicts itself: fail closed.
            return PwReading(PwReadingKind.UNKNOWN, None, None)
        return PwReading(PwReadingKind.COUNT, metric_events, measurement)
    if bucket.values_measurement is PwBucketMeasurement.NOT_COLLECTED and not (
        pinned and bucket.pinned_measurement is PwPinnedMeasurement.EXACT
    ):
        return _NO_BUCKET
    if not tracked_at_bucket:
        return _NOT_AN_OBSERVATION
    if measurement is WafMeasurement.EXACT:
        return PwReading(PwReadingKind.MEASURED_ZERO, 0, WafMeasurement.EXACT)
    return PwReading(PwReadingKind.UNKNOWN, None, measurement)


def absence_is_provable(reading: PwReading, bucket: PwBucketFact, coverage_floor: float) -> bool:
    """Test-only helper. Whether a verdict may read this reading as the value being absent.

    A zero is a zero of *parsed* events, so the verdicts that read absence also need
    coverage at the floor (design §G.5): a parsing break must not look like a fleet of
    disappearing values.
    """
    return reading.is_measured_zero and bucket.coverage.reaches(coverage_floor)
