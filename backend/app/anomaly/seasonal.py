"""Seasonal-baseline semantics shared by every behavior domain.

Pure functions, no I/O and no ORM. Each domain keeps its own metric and baseline
tables -- a WAF endpoint is not a log source and must not be squeezed into one --
but the rules for turning observations into a baseline cell are the same
everywhere, and two copies of them would drift:

  * the seasonal key is ISO weekday x hour of day, in UTC;
  * a cell's statistics are median, MAD and p05/p95 over the retained samples,
    and the cell drives no verdict below the minimum sample count;
  * a known anomaly's buckets are excluded so an incident cannot poison the
    baseline it is judged against;
  * baseline completeness is the fraction of candidate buckets that survived
    exclusion.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.anomaly import statistics as rstats

#: Bound on the observation sample stored with a cell, so a hot cell does not
#: bloat its row. The statistics are computed over every retained value.
MAX_STORED_OBSERVATIONS = 200


@dataclass(frozen=True)
class CellSummary:
    median: float
    mad: float
    p05: float
    p95: float
    sample_count: int
    #: False below the minimum sample count: the cell is stored and displayed
    #: as still learning, but must not drive a verdict.
    is_reliable: bool
    observations: list[float]


def seasonal_key(bucket_start: datetime) -> tuple[int, int]:
    """(ISO weekday 1-7, hour 0-23) of a UTC bucket start."""
    return bucket_start.isoweekday(), bucket_start.hour


def summarize_cell(values: Sequence[float], *, min_samples: int) -> CellSummary:
    """Robust statistics for one seasonal cell. `values` must be non-empty."""
    samples = list(values)
    med = rstats.median(samples)
    return CellSummary(
        median=med,
        mad=rstats.mad(samples, med),
        p05=rstats.percentile(samples, 5),
        p95=rstats.percentile(samples, 95),
        sample_count=len(samples),
        is_reliable=len(samples) >= min_samples,
        observations=[round(v, 4) for v in samples[-MAX_STORED_OBSERVATIONS:]],
    )


def retained_fraction(retained: int, candidates: int) -> float:
    """Share of candidate buckets that survived exclusion; 0.0 with none.

    A cell built from 4 of 28 buckets is far weaker than one built from 26, even
    when both clear the minimum sample count.
    """
    return retained / candidates if candidates else 0.0


def anomaly_exclusion_span(
    detected_at: datetime,
    anomaly_end: datetime | None,
    resolved_at: datetime | None,
    window_end: datetime,
) -> tuple[datetime, datetime]:
    """The interval an incident removes from its entity's baseline.

    A resolved incident excludes through its resolution, so the recovery buckets
    that confirmed it are not re-used as normal samples. An unresolved incident
    excludes only through `anomaly_end`, the end of its abnormal telemetry.
    Bounding an unresolved incident by the lookback end instead deadlocks it: it
    would exclude the very buckets its own recovery must be judged on, and a
    CANDIDATE that returned to normal -- never resolved, by design -- would
    poison the baseline forever (commit 4892f8e). Only an incident with neither
    timestamp, still genuinely in progress, excludes to the window end.
    """
    return detected_at, resolved_at or anomaly_end or window_end


def in_any_span(when: datetime, spans: Sequence[tuple[datetime, datetime]]) -> bool:
    """Whether `when` falls inside any closed span."""
    return any(lo <= when <= hi for lo, hi in spans)


def in_maintenance(
    bucket_start: datetime,
    *,
    maintenance_mode: bool,
    maintenance_until: datetime | None,
) -> bool:
    """Whether a bucket falls in its source's configured maintenance window.

    The one maintenance rule every behavior domain applies. It encodes the
    Phase A semantics exactly, over the two columns `LogSource` carries:

      * `maintenance_mode` off: never in maintenance.
      * on, with no `maintenance_until`: every bucket is in maintenance.
      * on, with `maintenance_until`: a bucket is in maintenance when it starts
        at or before `maintenance_until`. A bucket the window's end falls inside
        -- partial overlap -- is therefore in maintenance, and so is the bucket
        starting exactly at `maintenance_until`. The first bucket that is not
        is the first to start strictly after it.

    The window has no start, so the answer depends on *when* the rule is asked.
    Phase A asks it against the current flag every time it is used. The WAF
    domain asks it once, when a bucket is collected, and records the answer; see
    docs/PHASE-B-WAF-BEHAVIOR.md section 28.4 for why and for the Phase A
    consequence.
    """
    if not maintenance_mode:
        return False
    return maintenance_until is None or bucket_start <= maintenance_until
