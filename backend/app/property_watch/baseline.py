"""Pure seasonal baseline construction for property-watch values (design §G.8.0)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from app.anomaly.seasonal import in_any_span, retained_fraction, seasonal_key, summarize_cell
from app.models.enums import WafMeasurement
from app.property_watch.facts import PwBucketFact, PwReading, PwReadingKind
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC

PW_VALUE_EVENTS_SEMANTICS = "property_watch/value_events/v1"


@dataclass(frozen=True)
class PwBaselineReading:
    bucket: PwBucketFact
    reading: PwReading


@dataclass
class PwExclusionTally:
    """Why candidate readings did not train their weekday/hour cell."""

    lower_bound: int = 0
    unknown: int = 0
    low_coverage: int = 0
    scope_changed: int = 0
    maintenance: int = 0
    version_mismatch: int = 0
    known_anomaly: int = 0

    @property
    def total(self) -> int:
        return sum(self.as_dict().values())

    def as_dict(self) -> dict[str, int]:
        return {
            "lower_bound": self.lower_bound,
            "unknown": self.unknown,
            "low_coverage": self.low_coverage,
            "scope_changed": self.scope_changed,
            "maintenance": self.maintenance,
            "version_mismatch": self.version_mismatch,
            "known_anomaly": self.known_anomaly,
        }


@dataclass(frozen=True)
class PwBaselineCell:
    metric_name: str
    weekday: int
    hour: int
    median: float
    mad: float
    p05: float
    p95: float
    sample_count: int
    is_reliable: bool
    completeness: float
    observations: list[float]
    watch_version: int
    metric_semantics: str = PW_VALUE_EVENTS_SEMANTICS
    baseline_version: int = 1


def build_value_baseline(
    readings: Sequence[PwBaselineReading],
    *,
    watch_version: int,
    min_coverage: float,
    baseline_min_samples: int,
    baseline_min_visits: int = 1,
    incident_spans: Sequence[tuple[datetime, datetime]] = (),
) -> tuple[dict[tuple[int, int], PwBaselineCell], PwExclusionTally]:
    """Build every non-empty ``PW_VALUE_EVENTS_METRIC`` cell and its exclusion tally.

    Thin cells are deliberately returned with ``is_reliable=False``. Persistence can
    therefore show learning progress without allowing the detector to use the cell.

    A cell is also unreliable until its retained samples come from ``baseline_min_visits``
    distinct weekly visits (D-30): the 12 five-minute samples of one hour are correlated,
    not a baseline. A visit is the UTC calendar date of the bucket start, the same clock
    ``seasonal_key`` reads. Only retained samples count toward visits.
    """
    if watch_version < 1:
        raise ValueError("a watch version starts at 1")
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("coverage must be between zero and one")
    if baseline_min_samples < 1:
        raise ValueError("baseline_min_samples must be positive")
    if baseline_min_visits < 1:
        raise ValueError("baseline_min_visits must be positive")

    tally = PwExclusionTally()
    retained: dict[tuple[int, int], list[float]] = {}
    visits: dict[tuple[int, int], set[date]] = {}
    candidates: dict[tuple[int, int], int] = {}
    for item in readings:
        bucket, reading = item.bucket, item.reading
        if reading.kind is PwReadingKind.NOT_AN_OBSERVATION:
            continue  # the value was not tracked here: not a sample, not an exclusion
        key = seasonal_key(bucket.bucket_start)
        candidates[key] = candidates.get(key, 0) + 1
        if reading.measurement is WafMeasurement.LOWER_BOUND:
            tally.lower_bound += 1
        elif reading.events is None or reading.measurement is None:
            tally.unknown += 1
        elif not bucket.coverage.reaches(min_coverage):
            tally.low_coverage += 1
        elif bucket.scope_changed:
            tally.scope_changed += 1
        elif bucket.in_maintenance:
            tally.maintenance += 1
        elif bucket.watch_version != watch_version:
            tally.version_mismatch += 1
        elif in_any_span(bucket.bucket_start, incident_spans):
            tally.known_anomaly += 1
        else:
            retained.setdefault(key, []).append(float(reading.events))
            visits.setdefault(key, set()).add(bucket.bucket_start.date())

    cells: dict[tuple[int, int], PwBaselineCell] = {}
    for (weekday, hour), values in retained.items():
        summary = summarize_cell(values, min_samples=baseline_min_samples)
        cells[(weekday, hour)] = PwBaselineCell(
            metric_name=PW_VALUE_EVENTS_METRIC,
            weekday=weekday,
            hour=hour,
            median=summary.median,
            mad=summary.mad,
            p05=summary.p05,
            p95=summary.p95,
            sample_count=summary.sample_count,
            is_reliable=summary.is_reliable and len(visits[(weekday, hour)]) >= baseline_min_visits,
            completeness=retained_fraction(len(values), candidates[(weekday, hour)]),
            observations=summary.observations,
            watch_version=watch_version,
        )
    return cells, tally
