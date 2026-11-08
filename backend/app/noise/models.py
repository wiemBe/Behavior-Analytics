"""Shadow count-model view for one log source (SOC-17e, design §6.2).

Pure functions fit a Poisson or negative-binomial model to one baseline cell's
stored observations, score the last 28 days of bucket counts against it, and
describe shrinkage and a level shift. The loader reads; it never writes, never
opens an alert and never changes a detector.

Tails are a Poisson or negative-binomial CDF. scipy is not a project dependency,
so the CDF is ``math.lgamma`` plus a log-sum-exp. A model that does not fit
yields ``INSUFFICIENT_DATA`` and null tail counts: a missing probability is
never reported as zero.

Two exits sit beside the four the task names, because a number there would be
invented. Observations that are not non-negative integers are
``noise.model.not_counts``. A negative-binomial family whose method of moments
is undefined (under-dispersion, variance at or below the mean) is
``noise.model.nb_parameters``: the family name is reported and the tails are
withheld.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.detectors import MetricPoint
from app.anomaly.seasonal import in_maintenance
from app.anomaly.statistics import median as robust_median
from app.anomaly.thresholds import ThresholdResolver, direction_excluded
from app.core.config import Settings, get_settings
from app.custom_rules.preview import bucket_grid, latest_closed_bucket
from app.models.enums import AnomalyType, OutagePolicyMode
from app.models.log_source import LogSource, LogSourceBaseline
from app.models.outage import OutageAlertPolicy
from app.noise.backtest import (
    MAX_BUCKETS,
    MAX_NOT_JUDGED_FRACTION,
    MIN_COVERED_DAYS,
    REASON_DIRECTION_DISABLED,
    REASON_NOT_REPLAYABLE,
    REASON_TOO_LARGE,
    BucketInput,
    NoiseBacktest,
    ReplayReason,
    ReplayTrace,
    _covered_days,
    _thresholds,
    bucket_judged,
    reason,
    replay_trace,
)
from app.noise.read_model import NoiseIncidentSpan, NoiseReadModel

#: Poisson when the dispersion (variance / mean) sits in this closed interval.
POISSON_DISPERSION_MIN = 0.8
POISSON_DISPERSION_MAX = 1.25
#: Wider than this, the cell is not a count process this view will score.
DISPERSION_LIMIT = 4.0
#: A bucket tail at or above this is not counted. The design's p is 0.001.
P_THRESHOLD = 0.001
#: Shrinkage prior weight. The reported weight is n / (n + k), on the cell.
SHRINKAGE_K = 8
#: Samples required on each side of a level-shift proposal. Not baseline_min_samples.
LEVEL_SHIFT_MIN = 8
#: The shadow view is the 28-day window. It is not a caller-chosen preview length.
MODEL_WINDOW_DAYS = 28
#: Stored observations are rounded to 4 decimals. This accepts an integer that
#: survived that rounding and rejects a rate such as 50.25.
COUNT_TOLERANCE = 1e-6
#: A log probability below this is treated as zero mass, so a far tail does not
#: underflow a sum into an exception.
_LOG_FLOOR = -700.0

MODEL_POISSON = "poisson"
MODEL_NB = "negative_binomial"

REASON_MODEL_NOT_MONITORED = "noise.model.not_monitored"
REASON_NO_BASELINE = "noise.model.no_baseline"
REASON_MODEL_NOT_JUDGED = "noise.model.not_judged"
REASON_BUCKETS_NOT_JUDGED = "noise.model.buckets_not_judged"
REASON_TOO_FEW = "noise.model.too_few_samples"
REASON_UNRELIABLE = "noise.model.unreliable"
REASON_MEAN_ZERO = "noise.model.mean_zero"
REASON_NO_VARIANCE = "noise.model.no_variance"
REASON_NOT_COUNTS = "noise.model.not_counts"
REASON_DISPERSION = "noise.model.dispersion"
REASON_NB_PARAMETERS = "noise.model.nb_parameters"
REASON_PARTIAL = "noise.model.partial"
REASON_COVERAGE = "noise.model.insufficient_coverage"

_NO_BUCKET = "no stored bucket could be judged"


class ModelState(StrEnum):
    """Whether a cell, or the source, can state a count-model result."""

    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_JUDGED = "NOT_JUDGED"
    PARTIAL = "PARTIAL"


# ---------------------------------------------------------------- tails


def count_family(dispersion: float) -> str:
    """``poisson`` on the closed band, otherwise ``negative_binomial``."""
    if POISSON_DISPERSION_MIN <= dispersion <= POISSON_DISPERSION_MAX:
        return MODEL_POISSON
    return MODEL_NB


def is_count(value: float) -> bool:
    """True when ``value`` is a finite non-negative integer, within tolerance."""
    if not math.isfinite(value) or value < 0.0:
        return False
    return abs(value - round(value)) <= COUNT_TOLERANCE


def _logsumexp(terms: Sequence[float]) -> float:
    if not terms:
        return float("-inf")
    top = max(terms)
    if top == float("-inf") or top < _LOG_FLOOR:
        return float("-inf")
    total = math.fsum(math.exp(term - top) for term in terms)
    if total <= 0.0:
        return float("-inf")
    return top + math.log(total)


def _right_limit(mean: float, variance: float) -> float:
    """A count this far above the mean has an upper tail too small to sum."""
    return mean + 40.0 * math.sqrt(max(variance, 0.0)) + 40.0


def _log_poisson(k: int, mu: float) -> float:
    if k < 0:
        return float("-inf")
    if mu == 0.0:
        return 0.0 if k == 0 else float("-inf")
    return k * math.log(mu) - mu - math.lgamma(k + 1)


def poisson_cdf(k: int, mu: float) -> float:
    """P(X <= k) for X ~ Poisson(mu)."""
    if not math.isfinite(mu) or mu < 0.0:
        raise ValueError("mu must be a non-negative finite number")
    if k < 0:
        return 0.0
    if mu == 0.0:
        return 1.0
    if k > _right_limit(mu, mu):
        return 1.0
    total = _logsumexp([_log_poisson(i, mu) for i in range(k + 1)])
    if total == float("-inf"):
        return 0.0
    return min(1.0, math.exp(total))


def poisson_upper(k: int, mu: float) -> float:
    """P(X >= k) for X ~ Poisson(mu)."""
    if k <= 0:
        return 1.0
    if not math.isfinite(mu) or mu < 0.0:
        raise ValueError("mu must be a non-negative finite number")
    if mu == 0.0 or k > _right_limit(mu, mu):
        return 0.0
    return max(0.0, 1.0 - poisson_cdf(k - 1, mu))


def _log_nb(k: int, successes: float, p: float) -> float:
    """Log pmf of the number of failures before ``successes`` successes."""
    if k < 0:
        return float("-inf")
    return (
        math.lgamma(k + successes)
        - math.lgamma(k + 1)
        - math.lgamma(successes)
        + k * math.log1p(-p)
        + successes * math.log(p)
    )


def _nb_mean_variance(successes: float, p: float) -> tuple[float, float]:
    mean = successes * (1.0 - p) / p
    return mean, mean / p


def nb_cdf(k: int, r: float, p: float) -> float:
    """P(X <= k) for X ~ NB(r, p), failures before r successes."""
    if not math.isfinite(r) or not math.isfinite(p) or r <= 0.0 or not 0.0 < p < 1.0:
        raise ValueError("negative binomial parameters are not usable")
    if k < 0:
        return 0.0
    mean, variance = _nb_mean_variance(r, p)
    if k > _right_limit(mean, variance):
        return 1.0
    total = _logsumexp([_log_nb(i, r, p) for i in range(k + 1)])
    if total == float("-inf"):
        return 0.0
    return min(1.0, math.exp(total))


def nb_upper(k: int, r: float, p: float) -> float:
    """P(X >= k) for the same negative binomial."""
    if k <= 0:
        return 1.0
    if not math.isfinite(r) or not math.isfinite(p) or r <= 0.0 or not 0.0 < p < 1.0:
        raise ValueError("negative binomial parameters are not usable")
    mean, variance = _nb_mean_variance(r, p)
    if k > _right_limit(mean, variance):
        return 0.0
    return max(0.0, 1.0 - nb_cdf(k - 1, r, p))


def negative_binomial_parameters(mean: float, variance: float) -> tuple[float, float] | None:
    """Method of moments. None when r is not positive or p is not in (0, 1)."""
    if mean <= 0.0 or variance <= mean:
        return None
    p = mean / variance
    successes = mean * mean / (variance - mean)
    if successes <= 0.0 or not 0.0 < p < 1.0:
        return None
    return successes, p


def tail_counts(
    values: Sequence[int],
    *,
    model: str,
    mean: float,
    nb_r: float | None = None,
    nb_p: float | None = None,
) -> tuple[int, int]:
    """How many counts have an upper, then a lower, tail strictly below ``P_THRESHOLD``."""
    upper = 0
    lower = 0
    for k in values:
        if model == MODEL_POISSON:
            low = poisson_cdf(k, mean)
            high = poisson_upper(k, mean)
        else:
            if nb_r is None or nb_p is None:
                raise ValueError("negative binomial parameters are required")
            low = nb_cdf(k, nb_r, nb_p)
            high = nb_upper(k, nb_r, nb_p)
        if low < P_THRESHOLD:
            lower += 1
        if high < P_THRESHOLD:
            upper += 1
    return upper, lower


# ---------------------------------------------------------------- fit


@dataclass(frozen=True)
class CountFit:
    """The model fit to one cell's observations. Tails are scored separately."""

    usable: bool
    reason: ReplayReason | None
    mean: float | None
    variance: float | None
    dispersion: float | None
    model: str | None
    nb_r: float | None
    nb_p: float | None


def _unusable(
    why: ReplayReason,
    *,
    mean: float | None = None,
    variance: float | None = None,
    dispersion: float | None = None,
    model: str | None = None,
) -> CountFit:
    return CountFit(
        usable=False,
        reason=why,
        mean=mean,
        variance=variance,
        dispersion=dispersion,
        model=model,
        nb_r=None,
        nb_p=None,
    )


def _as_floats(observations: Sequence[object]) -> list[float] | None:
    values: list[float] = []
    for item in observations:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        values.append(float(item))
    return values


def fit_observations(
    observations: Sequence[object],
    *,
    sample_count: int,
    reliable: bool,
    min_samples: int,
) -> CountFit:
    """Fit one cell, or refuse. The order is the order the exits are pinned in.

    Moments use the population divisor (sum of squares over n), which is the
    method-of-moments variance. Dispersion is null when the mean is 0: it is
    not reported as 0, because 0 would be an invented ratio.
    """
    observed_n = len(observations)
    if observed_n < min_samples or sample_count < min_samples:
        return _unusable(
            reason(REASON_TOO_FEW, count=min(observed_n, sample_count), minimum=min_samples)
        )
    if not reliable:
        return _unusable(reason(REASON_UNRELIABLE))
    parsed = _as_floats(observations)
    if parsed is None or any(not math.isfinite(value) for value in parsed):
        return _unusable(reason(REASON_NO_VARIANCE))
    if len(parsed) < 2:
        if len(parsed) == 1 and parsed[0] == 0.0:
            return _unusable(reason(REASON_MEAN_ZERO), mean=0.0)
        return _unusable(reason(REASON_NO_VARIANCE), mean=parsed[0] if parsed else None)
    mean = sum(parsed) / len(parsed)
    variance = sum((value - mean) ** 2 for value in parsed) / len(parsed)
    if mean == 0.0:
        return _unusable(reason(REASON_MEAN_ZERO), mean=0.0, variance=variance)
    if any(not is_count(value) for value in parsed):
        return _unusable(
            reason(REASON_NOT_COUNTS),
            mean=mean,
            variance=variance,
            dispersion=variance / mean,
        )
    dispersion = variance / mean
    if dispersion > DISPERSION_LIMIT:
        return _unusable(
            reason(REASON_DISPERSION, dispersion=dispersion),
            mean=mean,
            variance=variance,
            dispersion=dispersion,
        )
    family = count_family(dispersion)
    if family == MODEL_NB:
        params = negative_binomial_parameters(mean, variance)
        if params is None:
            return _unusable(
                reason(REASON_NB_PARAMETERS),
                mean=mean,
                variance=variance,
                dispersion=dispersion,
                model=MODEL_NB,
            )
        nb_r, nb_p = params
        return CountFit(
            usable=True,
            reason=None,
            mean=mean,
            variance=variance,
            dispersion=dispersion,
            model=MODEL_NB,
            nb_r=nb_r,
            nb_p=nb_p,
        )
    return CountFit(
        usable=True,
        reason=None,
        mean=mean,
        variance=variance,
        dispersion=dispersion,
        model=MODEL_POISSON,
        nb_r=None,
        nb_p=None,
    )


# ---------------------------------------------------------------- display-only descriptions


@dataclass(frozen=True)
class Shrinkage:
    """The shrunk median. ``weight`` is the weight on the cell, n / (n + k)."""

    n: int
    k: int
    raw_median: float | None
    pooled_median: float | None
    weight: float
    estimate: float | None


def shrink_median(
    n: int,
    cell_median: float | None,
    pooled_median: float | None,
    *,
    k: int = SHRINKAGE_K,
) -> Shrinkage:
    """``n/(n+k) * cell + k/(n+k) * pooled``. Nothing is invented.

    n = 0 puts the whole weight on the pooled median, including when the cell
    median itself is missing. A missing side at n > 0 leaves the estimate null.
    """
    samples = n if n > 0 else 0
    weight = samples / (samples + k) if samples + k else 0.0
    if samples == 0 or cell_median is None or pooled_median is None:
        estimate = pooled_median if samples == 0 else None
    else:
        estimate = weight * cell_median + (1.0 - weight) * pooled_median
    return Shrinkage(
        n=samples,
        k=k,
        raw_median=cell_median,
        pooled_median=pooled_median,
        weight=weight,
        estimate=estimate,
    )


@dataclass(frozen=True)
class LevelShift:
    """A proposal that the recent week sits outside the cell's own p05/p95."""

    recent_median: float
    cell_median: float
    p05: float
    p95: float
    recent_samples: int
    cell_samples: int
    started_at: datetime


def propose_level_shift(
    recent: Sequence[tuple[datetime, float]],
    *,
    cell_median: float | None,
    p05: float | None,
    p95: float | None,
    cell_samples: int,
) -> LevelShift | None:
    """A shift strictly beyond p05 or p95, with at least 8 samples each side.

    ``started_at`` is the earliest recent bucket whose value is strictly beyond
    the bound the median crossed. Equal to the bound is not a shift. Fewer than
    8 samples on either side returns None, not a proposal with a short side.
    """
    if cell_median is None or p05 is None or p95 is None:
        return None
    finite = [
        (moment, float(value))
        for moment, value in recent
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    ]
    if len(finite) < LEVEL_SHIFT_MIN or cell_samples < LEVEL_SHIFT_MIN:
        return None
    ordered = sorted(finite, key=lambda item: item[0])
    recent_median = robust_median([value for _, value in ordered])
    if recent_median < p05:
        beyond = [moment for moment, value in ordered if value < p05]
    elif recent_median > p95:
        beyond = [moment for moment, value in ordered if value > p95]
    else:
        return None
    if not beyond:
        return None
    return LevelShift(
        recent_median=recent_median,
        cell_median=cell_median,
        p05=p05,
        p95=p95,
        recent_samples=len(ordered),
        cell_samples=cell_samples,
        started_at=beyond[0],
    )


def flags_on_noise(
    times: Sequence[datetime],
    spans: Sequence[tuple[datetime, datetime]],
) -> int:
    """How many flag times fall inside a NOISE span, each flag counted once.

    The comparison is inclusive of both ends. Spans that do not overlap a time
    add nothing, and two spans covering one time still count as one flag.
    """
    return sum(1 for moment in times if any(start <= moment <= end for start, end in spans))


# ---------------------------------------------------------------- one cell, and the source


@dataclass(frozen=True)
class CellModel:
    """One baseline cell's count model, plus the display-only descriptions."""

    metric: str
    weekday: int
    hour: int
    state: ModelState
    reason: ReplayReason | None
    sample_count: int
    observation_count: int
    reliable: bool
    mean: float | None
    variance: float | None
    dispersion: float | None
    model: str | None
    nb_r: float | None
    nb_p: float | None
    buckets_scored: int | None
    upper_tail_below: int | None
    lower_tail_below: int | None
    shrinkage: Shrinkage
    level_shift: LevelShift | None


def _cell(
    *,
    metric: str,
    weekday: int,
    hour: int,
    state: ModelState,
    why: ReplayReason | None,
    sample_count: int,
    observation_count: int,
    reliable: bool,
    shrinkage: Shrinkage,
    level_shift: LevelShift | None,
    mean: float | None = None,
    variance: float | None = None,
    dispersion: float | None = None,
    model: str | None = None,
    nb_r: float | None = None,
    nb_p: float | None = None,
    buckets_scored: int | None = None,
    upper_tail_below: int | None = None,
    lower_tail_below: int | None = None,
) -> CellModel:
    return CellModel(
        metric=metric,
        weekday=weekday,
        hour=hour,
        state=state,
        reason=why,
        sample_count=sample_count,
        observation_count=observation_count,
        reliable=reliable,
        mean=mean,
        variance=variance,
        dispersion=dispersion,
        model=model,
        nb_r=nb_r,
        nb_p=nb_p,
        buckets_scored=buckets_scored,
        upper_tail_below=upper_tail_below,
        lower_tail_below=lower_tail_below,
        shrinkage=shrinkage,
        level_shift=level_shift,
    )


def build_cell(
    *,
    metric: str,
    weekday: int,
    hour: int,
    observations: Sequence[object],
    sample_count: int,
    reliable: bool,
    cell_median: float | None,
    p05: float | None,
    p95: float | None,
    pooled_median: float | None,
    bucket_values: Sequence[float | None],
    recent: Sequence[tuple[datetime, float]],
    min_samples: int,
    monitored: bool,
    covered_days: int,
    window_days: int,
) -> CellModel:
    """One cell. Not monitored wins over the fit. A failed fit keeps its reason.

    Tail counts are filled only for a usable model on a window of at least 7
    covered days, and only over complete non-negative integer buckets. A missing
    or incomplete bucket is a gap, not a zero. Shrinkage is display-only and is
    present even when the fit is refused. A level shift is too, except on a
    source that is not monitored, where it would read as a judgement.
    """
    stored = tuple(observations)
    shrink_n = sample_count if sample_count > 0 else len(stored)
    shrinkage = shrink_median(shrink_n, cell_median, pooled_median)
    shift = None
    if monitored:
        shift = propose_level_shift(
            recent,
            cell_median=cell_median,
            p05=p05,
            p95=p95,
            cell_samples=shrink_n,
        )
    identity = (metric, weekday, hour, sample_count, len(stored), reliable, shrinkage)
    if not monitored:
        return _from_fit(
            identity,
            state=ModelState.NOT_JUDGED,
            why=reason(REASON_MODEL_NOT_MONITORED),
            level_shift=None,
            fit=None,
        )
    fit = fit_observations(
        stored, sample_count=sample_count, reliable=reliable, min_samples=min_samples
    )
    if not fit.usable:
        return _from_fit(
            identity,
            state=ModelState.INSUFFICIENT_DATA,
            why=fit.reason,
            level_shift=shift,
            fit=fit,
        )
    if covered_days < MIN_COVERED_DAYS:
        return _from_fit(
            identity,
            state=ModelState.INSUFFICIENT_DATA,
            why=reason(REASON_COVERAGE, covered=covered_days, window=window_days),
            level_shift=shift,
            fit=fit,
        )
    scored = [
        round(value) for value in bucket_values if value is not None and is_count(value)
    ]
    gap = (
        len(bucket_values) == 0
        or len(scored) != len(bucket_values)
        or covered_days < window_days
    )
    upper: int | None = None
    lower: int | None = None
    buckets_scored: int | None = None
    if scored and fit.model is not None and fit.mean is not None:
        upper, lower = tail_counts(
            scored, model=fit.model, mean=fit.mean, nb_r=fit.nb_r, nb_p=fit.nb_p
        )
        buckets_scored = len(scored)
    if gap:
        return _from_fit(
            identity,
            state=ModelState.PARTIAL,
            why=reason(REASON_PARTIAL, covered=covered_days, window=window_days),
            level_shift=shift,
            fit=fit,
            buckets_scored=buckets_scored,
            upper_tail_below=upper,
            lower_tail_below=lower,
        )
    return _from_fit(
        identity,
        state=ModelState.OK,
        why=None,
        level_shift=shift,
        fit=fit,
        buckets_scored=buckets_scored,
        upper_tail_below=upper,
        lower_tail_below=lower,
    )


def _from_fit(
    identity: tuple[str, int, int, int, int, bool, Shrinkage],
    *,
    state: ModelState,
    why: ReplayReason | None,
    level_shift: LevelShift | None,
    fit: CountFit | None,
    buckets_scored: int | None = None,
    upper_tail_below: int | None = None,
    lower_tail_below: int | None = None,
) -> CellModel:
    metric, weekday, hour, sample_count, observation_count, reliable, shrinkage = identity
    return _cell(
        metric=metric,
        weekday=weekday,
        hour=hour,
        state=state,
        why=why,
        sample_count=sample_count,
        observation_count=observation_count,
        reliable=reliable,
        shrinkage=shrinkage,
        level_shift=level_shift,
        mean=None if fit is None else fit.mean,
        variance=None if fit is None else fit.variance,
        dispersion=None if fit is None else fit.dispersion,
        model=None if fit is None else fit.model,
        nb_r=None if fit is None else fit.nb_r,
        nb_p=None if fit is None else fit.nb_p,
        buckets_scored=buckets_scored,
        upper_tail_below=upper_tail_below,
        lower_tail_below=lower_tail_below,
    )


def overall_state(
    *,
    monitored: bool,
    has_cells: bool,
    has_reliable: bool,
    cells: Sequence[CellModel],
    covered_days: int,
    window_days: int,
    not_judged_buckets: int,
    total_buckets: int,
    too_large: ReplayReason | None = None,
) -> tuple[ModelState, ReplayReason | None]:
    """The source state. Maintenance is not an input: it refuses calibration only.

    Not judged here means the window cannot be judged: the source is not
    monitored, it has no baseline cell, no cell is reliable, or more than 10%
    of the covered buckets are not judged. It does not also require the current
    hour's average-eps cell, which is the list's single-cell rule.
    """
    if too_large is not None:
        return ModelState.INSUFFICIENT_DATA, too_large
    if not monitored:
        return ModelState.NOT_JUDGED, reason(REASON_MODEL_NOT_MONITORED)
    if not has_cells:
        return ModelState.NOT_JUDGED, reason(REASON_NO_BASELINE)
    if not has_reliable:
        return ModelState.NOT_JUDGED, reason(REASON_MODEL_NOT_JUDGED)
    if total_buckets and not_judged_buckets > MAX_NOT_JUDGED_FRACTION * total_buckets:
        return ModelState.NOT_JUDGED, reason(
            REASON_BUCKETS_NOT_JUDGED, count=not_judged_buckets, total=total_buckets
        )
    if covered_days < MIN_COVERED_DAYS:
        return ModelState.INSUFFICIENT_DATA, reason(
            REASON_COVERAGE, covered=covered_days, window=window_days
        )
    partial = covered_days < window_days or any(
        cell.state is ModelState.PARTIAL for cell in cells
    )
    if partial:
        return ModelState.PARTIAL, reason(
            REASON_PARTIAL, covered=covered_days, window=window_days
        )
    if any(cell.state is ModelState.OK for cell in cells):
        return ModelState.OK, None
    if cells and all(cell.state is ModelState.NOT_JUDGED for cell in cells):
        return ModelState.NOT_JUDGED, reason(REASON_MODEL_NOT_JUDGED)
    return ModelState.INSUFFICIENT_DATA, next(
        (cell.reason for cell in cells if cell.reason is not None), None
    )


@dataclass(frozen=True)
class CalibrationRow:
    """One detector's replayed flags and how many of them sit on a NOISE incident.

    A null count was not measured. ``excluded_by_direction`` is true only when
    the replay ran and the direction knob dropped the detector.
    """

    anomaly_type: AnomalyType
    replayed_flags: int | None
    noise_flags: int | None
    excluded_by_direction: bool
    reason: ReplayReason | None


def calibration_rows(
    *,
    refusal: ReplayReason | None,
    trace: ReplayTrace | None,
    direction: object | None,
    spans: Mapping[AnomalyType, Sequence[tuple[datetime, datetime]]],
) -> tuple[tuple[CalibrationRow, ...], ReplayReason | None]:
    """Per detector, in ``AnomalyType`` order. A refusal nulls every count.

    The refusal is the preview's own reason (maintenance, coverage, not judged,
    not monitored, too large). Direction is applied only once the replay ran.
    A detector that ran and flagged nothing reports 0. A detector that did not
    run reports null.
    """
    if refusal is not None:
        rows = tuple(
            CalibrationRow(
                anomaly_type=kind,
                replayed_flags=None,
                noise_flags=None,
                excluded_by_direction=False,
                reason=refusal,
            )
            for kind in AnomalyType
        )
        return rows, refusal
    if trace is None:
        raise ValueError("a replay refusal or a trace is required")
    excluded = direction_excluded(direction)
    rows_list: list[CalibrationRow] = []
    for kind in AnomalyType:
        if kind in excluded:
            rows_list.append(
                CalibrationRow(
                    anomaly_type=kind,
                    replayed_flags=None,
                    noise_flags=None,
                    excluded_by_direction=True,
                    reason=reason(REASON_DIRECTION_DISABLED),
                )
            )
            continue
        if trace.judged.get(kind, 0) <= 0:
            rows_list.append(
                CalibrationRow(
                    anomaly_type=kind,
                    replayed_flags=None,
                    noise_flags=None,
                    excluded_by_direction=False,
                    reason=reason(
                        REASON_NOT_REPLAYABLE,
                        reason=trace.skipped.get(kind, _NO_BUCKET),
                    ),
                )
            )
            continue
        times = trace.flags.get(kind, ())
        rows_list.append(
            CalibrationRow(
                anomaly_type=kind,
                replayed_flags=len(times),
                noise_flags=flags_on_noise(times, spans.get(kind, ())),
                excluded_by_direction=False,
                reason=None,
            )
        )
    return tuple(rows_list), None


@dataclass(frozen=True)
class ModelView:
    """The shadow view for one source. Every count it could not measure is null."""

    log_source_id: uuid.UUID
    state: ModelState
    reason: ReplayReason | None
    window_days: int
    covered_days: int
    first_bucket: datetime | None
    last_bucket: datetime | None
    p_threshold: float
    shrinkage_k: int
    cells: tuple[CellModel, ...]
    calibration: tuple[CalibrationRow, ...]
    calibration_reason: ReplayReason | None


def _observed(point: MetricPoint, metric: str) -> float:
    if metric == "event_count":
        return float(point.event_count)
    return float(point.average_eps)


def _pooled_medians(
    cells: Mapping[tuple[int, int, str], LogSourceBaseline],
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for (_weekday, _hour, metric), row in cells.items():
        grouped.setdefault(metric, []).append(float(row.median))
    return {metric: robust_median(values) for metric, values in grouped.items() if values}


class LowVolumeModel:
    """The four reads behind ``GET /noise/sources/{id}/model``.

    The first three are the preview's own loaders (source, metric rows, cells),
    so this view cannot drift onto a second history read. The fourth is the
    closed NOISE spans. A window that would exceed the preview's bucket cap
    returns after the source read, as the preview does. Every other existing
    source pays all four statements, including the ones whose replay is refused.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        now: datetime | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.now = now or datetime.now(UTC)
        # The backtest and the read model share this clock so the window and the
        # spans name the same 28 days.
        self._backtest = NoiseBacktest(session, settings=self.settings, now=self.now)
        self._read = NoiseReadModel(session, settings=self.settings, now=self.now)

    async def load(self, log_source_id: uuid.UUID) -> ModelView | None:
        """The view, or None when the source does not exist."""
        source_row = await self._backtest._source(log_source_id)
        if source_row is None:
            return None
        source, policies, source_class, latest_width = source_row
        interval = int(source.expected_interval_seconds or latest_width or 300)
        if interval <= 0:
            interval = 300
        estimated = (MODEL_WINDOW_DAYS * 86_400) // interval + 1
        if estimated > MAX_BUCKETS:
            return self._too_large(source, estimated, interval)

        grid = bucket_grid(self.now, MODEL_WINDOW_DAYS * 24, interval)
        if grid:
            window_start = grid[0]
            window_end = grid[-1] + timedelta(seconds=interval)
        else:
            window_start = self.now
            window_end = self.now
        # Statements 2, 3 and 4. Always all three, so a refused replay costs the
        # same as a full one. The metric read is the preview's loader.
        metrics = await self._backtest._metrics(
            log_source_id, window_start, window_end, interval
        )
        cells = await self._backtest._cells(log_source_id)
        spans = await self._read.noise_incident_spans(log_source_id, window_start, window_end)
        return self._assemble(
            source,
            policies,
            source_class,
            interval,
            grid,
            metrics,
            cells,
            spans,
            window_end,
        )

    def _too_large(self, source: LogSource, estimated: int, interval: int) -> ModelView:
        why = reason(REASON_TOO_LARGE, count=estimated, limit=MAX_BUCKETS)
        calibration, calibration_reason = calibration_rows(
            refusal=why, trace=None, direction=None, spans={}
        )
        return self._view(
            source.id,
            state=ModelState.INSUFFICIENT_DATA,
            why=why,
            covered_days=0,
            first=None,
            last=latest_closed_bucket(self.now, interval),
            cells=(),
            calibration=calibration,
            calibration_reason=calibration_reason,
        )

    def _view(
        self,
        log_source_id: uuid.UUID,
        *,
        state: ModelState,
        why: ReplayReason | None,
        covered_days: int,
        first: datetime | None,
        last: datetime | None,
        cells: Sequence[CellModel],
        calibration: tuple[CalibrationRow, ...],
        calibration_reason: ReplayReason | None,
    ) -> ModelView:
        return ModelView(
            log_source_id=log_source_id,
            state=state,
            reason=why,
            window_days=MODEL_WINDOW_DAYS,
            covered_days=covered_days,
            first_bucket=first,
            last_bucket=last,
            p_threshold=P_THRESHOLD,
            shrinkage_k=SHRINKAGE_K,
            cells=tuple(cells),
            calibration=calibration,
            calibration_reason=calibration_reason,
        )

    def _assemble(
        self,
        source: LogSource,
        policies: Sequence[OutageAlertPolicy],
        source_class: str | None,
        interval: int,
        grid: Sequence[datetime],
        metrics: Sequence[object],
        cells: Mapping[tuple[int, int, str], LogSourceBaseline],
        spans: Sequence[NoiseIncidentSpan],
        window_end: datetime,
    ) -> ModelView:
        buckets = self._backtest._bucket_inputs(source, metrics, cells, grid)  # type: ignore[arg-type]
        stored = [bucket.start for bucket in buckets if bucket is not None]
        covered_days = _covered_days(stored, interval, MODEL_WINDOW_DAYS)
        present = [bucket for bucket in buckets if bucket is not None]
        covered_grid = (
            [start for start in grid if stored[0] <= start <= stored[-1]] if stored else []
        )
        not_judged = len(covered_grid) - sum(1 for bucket in present if bucket_judged(bucket))
        by_start = {bucket.start: bucket for bucket in present}
        pooled = _pooled_medians(cells)
        recent_after = window_end - timedelta(days=7)
        built: list[CellModel] = []
        for (weekday, hour, metric), row in sorted(cells.items(), key=_cell_sort):
            values, recent = _cell_buckets(
                grid, by_start, weekday, hour, metric, recent_after
            )
            observations = tuple(row.observations or [])
            built.append(
                build_cell(
                    metric=metric,
                    weekday=weekday,
                    hour=hour,
                    observations=observations,
                    sample_count=int(row.sample_count),
                    reliable=bool(row.is_reliable),
                    cell_median=float(row.median),
                    p05=None if row.p05 is None else float(row.p05),
                    p95=None if row.p95 is None else float(row.p95),
                    pooled_median=pooled.get(metric),
                    bucket_values=values,
                    recent=recent,
                    min_samples=self.settings.baseline_min_samples,
                    monitored=bool(source.monitoring_enabled),
                    covered_days=covered_days,
                    window_days=MODEL_WINDOW_DAYS,
                )
            )
        maintained = in_maintenance(
            self.now,
            maintenance_mode=bool(source.maintenance_mode),
            maintenance_until=source.maintenance_until,
        )
        refusal = self._backtest._insufficient(
            source=source,
            maintained=maintained,
            covered_days=covered_days,
            window_days=MODEL_WINDOW_DAYS,
            not_judged=not_judged,
            total_buckets=len(covered_grid),
        )
        span_map = _spans_by_type(spans)
        if refusal is None:
            thresholds, _by_type = _thresholds(
                ThresholdResolver(self.settings),
                source,
                dict(source.custom_thresholds or {}),
                policies,
                source_class,
                OutagePolicyMode(self.settings.outage_alert_default),
            )
            trace = replay_trace(present, thresholds=thresholds)
            calibration, calibration_reason = calibration_rows(
                refusal=None,
                trace=trace,
                direction=thresholds.direction,
                spans=span_map,
            )
        else:
            calibration, calibration_reason = calibration_rows(
                refusal=refusal, trace=None, direction=None, spans=span_map
            )
        state, why = overall_state(
            monitored=bool(source.monitoring_enabled),
            has_cells=bool(cells),
            has_reliable=any(bool(row.is_reliable) for row in cells.values()),
            cells=built,
            covered_days=covered_days,
            window_days=MODEL_WINDOW_DAYS,
            not_judged_buckets=not_judged,
            total_buckets=len(covered_grid),
        )
        return self._view(
            source.id,
            state=state,
            why=why,
            covered_days=covered_days,
            first=stored[0] if stored else None,
            last=stored[-1] if stored else None,
            cells=built,
            calibration=calibration,
            calibration_reason=calibration_reason,
        )


def _cell_sort(item: tuple[tuple[int, int, str], LogSourceBaseline]) -> tuple[str, int, int]:
    weekday, hour, metric = item[0]
    return metric, weekday, hour


def _cell_buckets(
    grid: Sequence[datetime],
    by_start: Mapping[datetime, BucketInput],
    weekday: int,
    hour: int,
    metric: str,
    recent_after: datetime,
) -> tuple[list[float | None], list[tuple[datetime, float]]]:
    values: list[float | None] = []
    recent: list[tuple[datetime, float]] = []
    for start in grid:
        if (start.isoweekday(), start.hour) != (weekday, hour):
            continue
        bucket = by_start.get(start)
        if bucket is None or not bucket.complete:
            values.append(None)
            continue
        raw = _observed(bucket.point, metric)
        values.append(raw)
        if start >= recent_after:
            recent.append((start, raw))
    return values, recent


def _spans_by_type(
    spans: Sequence[NoiseIncidentSpan],
) -> dict[AnomalyType, tuple[tuple[datetime, datetime], ...]]:
    grouped: dict[AnomalyType, list[tuple[datetime, datetime]]] = {}
    for span in spans:
        grouped.setdefault(span.anomaly_type, []).append((span.started_at, span.ended_at))
    return {kind: tuple(items) for kind, items in grouped.items()}
