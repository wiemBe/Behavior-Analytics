"""Guards for the shadow count model (SOC-17e).

The tail probabilities are checked against a recurrence that does not call
``math.lgamma``, so a copied constant in the production CDF cannot satisfy
them. Every fail-closed exit is scored through ``build_cell`` with bucket
values present: an unfit model that still emits a tail count fails the test.
"""

from __future__ import annotations

import math
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.anomaly.detectors import BaselineCell, MetricPoint
from app.anomaly.thresholds import Thresholds
from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.enums import AnomalyType, BucketCompleteness
from app.noise.backtest import BucketInput, ReplayTrace, fold_buckets, replay_trace
from app.noise.models import (
    LEVEL_SHIFT_MIN,
    SHRINKAGE_K,
    CountFit,
    ModelState,
    build_cell,
    calibration_rows,
    count_family,
    fit_observations,
    flags_on_noise,
    is_count,
    nb_cdf,
    overall_state,
    poisson_cdf,
    poisson_upper,
    propose_level_shift,
    shrink_median,
    tail_counts,
)
from app.providers.base import LogSourceTelemetry

START = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
POISSON_SAMPLE = [0, 1, 2, 2, 3, 3, 4, 4, 5, 6]


def _poisson_cdf_recurrence(k: int, mu: float) -> float:
    """P(X <= k) by the factorial recurrence. This does not use math.lgamma."""
    if k < 0:
        return 0.0
    term = math.exp(-mu)
    total = term
    for index in range(1, k + 1):
        term *= mu / index
        total += term
    return total


def _dispersion(values: list[float]) -> float:
    """Population dispersion, divided by n, computed here and not by the fitter."""
    count = len(values)
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    return variance / mean


def _build(**overrides: object) -> object:
    values: dict[str, object] = {
        "metric": "event_count",
        "weekday": 1,
        "hour": 10,
        "observations": list(POISSON_SAMPLE),
        "sample_count": 10,
        "reliable": True,
        "cell_median": 3.0,
        "p05": 0.0,
        "p95": 6.0,
        "pooled_median": 4.0,
        "bucket_values": [3, 10, 12],
        "recent": [],
        "min_samples": 8,
        "monitored": True,
        "covered_days": 28,
        "window_days": 28,
    }
    values.update(overrides)
    return build_cell(**values)  # type: ignore[arg-type]


def _point(start: datetime, eps: float) -> MetricPoint:
    return MetricPoint(
        interval_start=start,
        interval_end=start + timedelta(seconds=300),
        completeness=BucketCompleteness.COMPLETE,
        event_count=int(eps * 300),
        average_eps=eps,
        peak_eps=eps,
        last_event_at=None,
        event_delay_seconds=1.0,
        unknown_event_count=0,
        stored_event_count=int(eps * 300),
        parsed_username_ratio=0.9,
        parsed_source_ip_ratio=0.9,
        distinct_qid_count=5,
        distinct_username_count=5,
        distinct_source_ip_count=5,
        collection_error_count=0,
        payload_signature=None,
    )


def _bucket(start: datetime, eps: float) -> BucketInput:
    eps_cell = BaselineCell(
        median=50.0, mad=2.0, p05=40.0, p95=60.0,
        sample_count=30, is_reliable=True, completeness=1.0, baseline_version=3,
    )
    count_cell = BaselineCell(
        median=15000.0, mad=100.0, p05=10000.0, p95=20000.0,
        sample_count=30, is_reliable=True, completeness=1.0, baseline_version=3,
    )
    return BucketInput(
        start=start,
        point=_point(start, eps),
        cells={"average_eps": eps_cell, "event_count": count_cell},
        expected_interval_seconds=300.0,
        recent_signatures=(),
        is_expected_active=True,
        preceding_empty_buckets=0,
        telemetry=frozenset({LogSourceTelemetry.EVENT_VOLUME}),
        complete=True,
    )


def test_poisson_upper_tail_matches_an_independent_recurrence() -> None:
    # Poisson(3): P(X >= 10) is about 0.001102, which is not below 0.001.
    independent_10 = 1.0 - _poisson_cdf_recurrence(9, 3.0)
    assert independent_10 == pytest.approx(0.001102, abs=5e-7)
    assert poisson_upper(10, 3.0) == pytest.approx(independent_10, abs=1e-12)
    assert poisson_cdf(9, 3.0) == pytest.approx(_poisson_cdf_recurrence(9, 3.0), abs=1e-12)
    independent_12 = 1.0 - _poisson_cdf_recurrence(11, 3.0)
    assert independent_12 < 0.001
    assert poisson_upper(12, 3.0) == pytest.approx(independent_12, abs=1e-12)
    upper, lower = tail_counts([10, 12], model="poisson", mean=3.0)
    assert upper == 1
    assert lower == 0


def test_negative_binomial_cdf_matches_a_closed_form() -> None:
    # r = 2, p = 0.5: P(X = k) = (k + 1) * (1/2) ** (k + 2).
    def pmf(k: int) -> float:
        return (k + 1) * (0.5 ** (k + 2))

    assert pmf(0) == pytest.approx(0.25)
    assert pmf(1) == pytest.approx(0.25)
    assert pmf(2) == pytest.approx(0.1875)
    assert pmf(3) == pytest.approx(0.125)
    closed = pmf(0) + pmf(1) + pmf(2) + pmf(3)
    assert closed == pytest.approx(0.8125)
    assert nb_cdf(3, 2.0, 0.5) == pytest.approx(closed, abs=1e-12)


def test_extreme_count_returns_without_overflow() -> None:
    started = time.perf_counter()
    upper = poisson_upper(10**6, 3.0)
    cdf = poisson_cdf(10**6, 3.0)
    elapsed = time.perf_counter() - started
    assert upper == 0.0
    assert cdf == 1.0
    assert elapsed < 0.05


@pytest.mark.parametrize(
    ("dispersion", "family"),
    [(0.79, "negative_binomial"), (0.8, "poisson"), (1.25, "poisson"), (1.26, "negative_binomial")],
)
def test_count_family_boundaries(dispersion: float, family: str) -> None:
    assert count_family(dispersion) == family


def test_fit_uses_count_family_on_a_poisson_sample() -> None:
    fit = fit_observations(
        POISSON_SAMPLE, sample_count=10, reliable=True, min_samples=8
    )
    assert fit.usable
    assert fit.model == count_family(_dispersion([float(item) for item in POISSON_SAMPLE]))
    assert fit.model == "poisson"
    assert fit.dispersion == pytest.approx(1.0)


def test_fit_uses_count_family_on_an_underdispersed_sample() -> None:
    values = [1, 1, 2, 2, 3, 3, 4, 4]
    fit = fit_observations(values, sample_count=8, reliable=True, min_samples=8)
    assert fit.model == count_family(_dispersion([float(item) for item in values]))
    assert fit.model == "negative_binomial"
    assert fit.usable is False
    assert fit.reason is not None
    assert fit.reason.code == "noise.model.nb_parameters"
    assert fit.nb_r is None and fit.nb_p is None


def test_dispersion_boundary_1_25_fits_poisson() -> None:
    # [0]*5 + [2, 2, 2]: mean 0.75, variance 0.9375, dispersion 1.25.
    values = [0, 0, 0, 0, 0, 2, 2, 2]
    fit = fit_observations(values, sample_count=8, reliable=True, min_samples=8)
    assert fit.usable
    assert fit.dispersion == pytest.approx(1.25)
    assert fit.model == "poisson"


def test_dispersion_of_exactly_4_is_a_usable_negative_binomial() -> None:
    values = [0, 0, 0, 0, 5]
    fit = fit_observations(values, sample_count=5, reliable=True, min_samples=5)
    assert fit.usable
    assert fit.dispersion == pytest.approx(4.0)
    assert fit.model == "negative_binomial"
    assert fit.nb_r is not None and fit.nb_p is not None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2.0, True), (2.0 + 1e-7, True), (2.0 + 1e-5, False), (-1.0, False), (1.5, False)],
)
def test_is_count_tolerance(value: float, expected: bool) -> None:
    assert is_count(value) is expected


def _assert_no_tails(cell: CountFit | object) -> None:
    assert cell.upper_tail_below is None  # type: ignore[attr-defined]
    assert cell.lower_tail_below is None  # type: ignore[attr-defined]


def test_too_few_samples_withholds_every_moment() -> None:
    cell = _build(observations=[1, 2, 3], sample_count=3)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.too_few_samples"  # type: ignore[attr-defined]
    assert cell.reason.params["count"] == 3  # type: ignore[attr-defined]
    assert cell.reason.params["minimum"] == 8  # type: ignore[attr-defined]
    assert cell.mean is None and cell.variance is None  # type: ignore[attr-defined]
    assert cell.dispersion is None and cell.model is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_unreliable_cell_withholds_moments() -> None:
    cell = _build(reliable=False)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.unreliable"  # type: ignore[attr-defined]
    assert cell.mean is None and cell.variance is None and cell.model is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_mean_zero_shows_the_measured_moments_and_no_tails() -> None:
    cell = _build(observations=[0] * 8, sample_count=8)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.mean_zero"  # type: ignore[attr-defined]
    assert cell.mean == 0.0  # type: ignore[attr-defined]
    assert cell.variance == 0.0  # type: ignore[attr-defined]
    assert cell.dispersion is None and cell.model is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_one_observation_of_zero_is_mean_zero_without_a_variance() -> None:
    cell = _build(observations=[0], sample_count=1, min_samples=1)
    assert cell.reason.code == "noise.model.mean_zero"  # type: ignore[attr-defined]
    assert cell.mean == 0.0 and cell.variance is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_one_observation_has_no_variance() -> None:
    cell = _build(observations=[5], sample_count=1, min_samples=1)
    assert cell.reason.code == "noise.model.no_variance"  # type: ignore[attr-defined]
    assert cell.mean == 5.0 and cell.variance is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_non_numeric_observations_are_no_variance() -> None:
    cell = _build(observations=["x"] * 8, sample_count=8)
    assert cell.reason.code == "noise.model.no_variance"  # type: ignore[attr-defined]
    assert cell.mean is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_bool_observations_are_not_counts() -> None:
    cell = _build(observations=[True] * 8, sample_count=8)
    assert cell.reason.code == "noise.model.no_variance"  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_mean_zero_wins_over_not_counts() -> None:
    cell = _build(observations=[-1, 1], sample_count=2, min_samples=2)
    assert cell.reason.code == "noise.model.mean_zero"  # type: ignore[attr-defined]
    assert cell.mean == 0.0  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_not_counts_withholds_tails_and_keeps_the_moments() -> None:
    cell = _build(observations=[50.5, 49.5] * 4, sample_count=8)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.not_counts"  # type: ignore[attr-defined]
    assert cell.mean is not None and cell.variance is not None  # type: ignore[attr-defined]
    assert cell.dispersion is not None and cell.model is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_not_counts_wins_over_a_wide_dispersion() -> None:
    cell = _build(observations=[0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 40.5], sample_count=8)
    assert cell.reason.code == "noise.model.not_counts"  # type: ignore[attr-defined]
    assert cell.model is None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_dispersion_above_4_withholds_the_family_and_the_tails() -> None:
    cell = _build(observations=[0, 0, 0, 0, 0, 0, 0, 8], sample_count=8)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.dispersion"  # type: ignore[attr-defined]
    assert cell.dispersion == pytest.approx(7.0)  # type: ignore[attr-defined]
    assert cell.model is None  # type: ignore[attr-defined]
    assert cell.mean == pytest.approx(1.0)  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_dispersion_of_5_is_insufficient() -> None:
    cell = _build(observations=[0, 0, 0, 0, 0, 6], sample_count=6, min_samples=6)
    assert cell.reason.code == "noise.model.dispersion"  # type: ignore[attr-defined]
    assert cell.dispersion == pytest.approx(5.0)  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_undefined_negative_binomial_parameters_withhold_tails() -> None:
    cell = _build(observations=[3] * 8, sample_count=8)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.nb_parameters"  # type: ignore[attr-defined]
    assert cell.model == "negative_binomial"  # type: ignore[attr-defined]
    assert cell.nb_r is None and cell.nb_p is None  # type: ignore[attr-defined]
    assert cell.mean == pytest.approx(3.0) and cell.variance == 0.0  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_valid_negative_binomial_scores_tails() -> None:
    cell = _build(
        observations=[0, 0, 0, 0, 5, 5, 5, 5],
        sample_count=8,
        bucket_values=[0, 5],
        covered_days=28,
    )
    assert cell.state is ModelState.OK  # type: ignore[attr-defined]
    assert cell.model == "negative_binomial"  # type: ignore[attr-defined]
    assert cell.nb_p == pytest.approx(0.4)  # type: ignore[attr-defined]
    assert cell.upper_tail_below is not None  # type: ignore[attr-defined]
    assert cell.lower_tail_below is not None  # type: ignore[attr-defined]


def test_shrinkage_at_zero_eight_and_two_hundred() -> None:
    empty = shrink_median(0, 10.0, 4.0)
    assert empty.weight == 0.0
    assert empty.estimate == 4.0
    assert empty.raw_median == 10.0
    missing = shrink_median(0, None, 4.0)
    assert missing.estimate == 4.0
    assert shrink_median(0, None, None).estimate is None
    half = shrink_median(8, 10.0, 4.0)
    assert half.weight == pytest.approx(0.5)
    assert half.estimate == pytest.approx(7.0)
    full = shrink_median(200, 10.0, 4.0)
    assert full.k == SHRINKAGE_K
    assert full.weight == pytest.approx(200 / 208)
    assert full.estimate == pytest.approx(200 / 208 * 10 + 8 / 208 * 4)
    assert shrink_median(8, None, 4.0).estimate is None
    assert shrink_median(8, 10.0, None).estimate is None
    assert shrink_median(8, 10.0, None).weight == pytest.approx(0.5)


def _recent(values: list[float]) -> list[tuple[datetime, float]]:
    return [(START + timedelta(hours=index), value) for index, value in enumerate(values)]


def test_level_shift_under_and_over_the_cell_band() -> None:
    under = propose_level_shift(
        _recent([1.0] * 8), cell_median=5.0, p05=2.0, p95=10.0, cell_samples=8
    )
    assert under is not None
    assert under.recent_median == pytest.approx(1.0)
    assert under.started_at == START
    # Median 9 is beyond p95; the mean of this sample is 6 and would not be.
    skewed = [1.0, 1.0, 1.0, 9.0, 9.0, 9.0, 9.0, 9.0]
    over = propose_level_shift(
        _recent(skewed), cell_median=5.0, p05=0.0, p95=8.0, cell_samples=8
    )
    assert over is not None
    assert over.recent_median == pytest.approx(9.0)
    assert sum(skewed) / len(skewed) == pytest.approx(6.0)


def test_level_shift_needs_eight_samples_on_each_side() -> None:
    values = [30.0] * 8
    assert propose_level_shift(
        _recent(values[:7]), cell_median=3.0, p05=0.0, p95=6.0, cell_samples=8
    ) is None
    assert propose_level_shift(
        _recent(values), cell_median=3.0, p05=0.0, p95=6.0, cell_samples=7
    ) is None
    assert LEVEL_SHIFT_MIN == 8
    proposed = propose_level_shift(
        _recent(values), cell_median=3.0, p05=0.0, p95=6.0, cell_samples=8
    )
    assert proposed is not None
    assert proposed.recent_samples == 8 and proposed.cell_samples == 8


def test_level_shift_equal_to_the_bound_is_not_a_proposal() -> None:
    assert propose_level_shift(
        _recent([10.0] * 8), cell_median=5.0, p05=0.0, p95=10.0, cell_samples=8
    ) is None


def test_level_shift_starts_at_the_earliest_bucket_beyond_the_bound() -> None:
    moments = _recent([5.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
    proposed = propose_level_shift(
        moments, cell_median=3.0, p05=0.0, p95=6.0, cell_samples=8
    )
    assert proposed is not None
    assert proposed.started_at == moments[1][0]


def test_short_coverage_withholds_tails_after_a_successful_fit() -> None:
    cell = _build(covered_days=3)
    assert cell.state is ModelState.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.insufficient_coverage"  # type: ignore[attr-defined]
    assert cell.model == "poisson"  # type: ignore[attr-defined]
    assert cell.mean is not None  # type: ignore[attr-defined]
    _assert_no_tails(cell)


def test_partial_window_scores_the_buckets_that_are_present() -> None:
    cell = _build(covered_days=14)
    assert cell.state is ModelState.PARTIAL  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.partial"  # type: ignore[attr-defined]
    assert cell.upper_tail_below is not None  # type: ignore[attr-defined]
    assert cell.buckets_scored == 3  # type: ignore[attr-defined]


def test_a_missing_bucket_makes_the_cell_partial() -> None:
    cell = _build(bucket_values=[3, None, 3], covered_days=28)
    assert cell.state is ModelState.PARTIAL  # type: ignore[attr-defined]
    assert cell.buckets_scored == 2  # type: ignore[attr-defined]
    assert cell.upper_tail_below is not None  # type: ignore[attr-defined]


def test_an_unmonitored_cell_is_not_judged_and_has_no_level_shift() -> None:
    recent = _recent([30.0] * 8)
    cell = _build(monitored=False, recent=recent)
    assert cell.state is ModelState.NOT_JUDGED  # type: ignore[attr-defined]
    assert cell.reason.code == "noise.model.not_monitored"  # type: ignore[attr-defined]
    assert cell.mean is None and cell.level_shift is None  # type: ignore[attr-defined]
    assert cell.shrinkage.raw_median == 3.0  # type: ignore[attr-defined]
    assert cell.shrinkage.estimate is not None  # type: ignore[attr-defined]


def test_a_full_window_with_every_slot_is_ok() -> None:
    cell = _build(bucket_values=[3, 3, 3])
    assert cell.state is ModelState.OK  # type: ignore[attr-defined]
    assert cell.reason is None  # type: ignore[attr-defined]
    assert cell.upper_tail_below == 0  # type: ignore[attr-defined]
    assert cell.lower_tail_below == 0  # type: ignore[attr-defined]


def test_flags_on_noise_counts_a_flag_once() -> None:
    second = START + timedelta(hours=1)
    third = START + timedelta(hours=2)
    times = [START, second, third]
    overlapped = flags_on_noise(times, [(START, START), (START, second)])
    assert overlapped == 2
    assert flags_on_noise(times, [(third, third)]) == 1
    assert flags_on_noise(times, []) == 0


def test_calibration_counts_only_noise_spans() -> None:
    second = START + timedelta(hours=1)
    trace = ReplayTrace(
        flags={AnomalyType.VOLUME_SPIKE: (START, second)},
        judged={AnomalyType.VOLUME_SPIKE: 4, AnomalyType.VOLUME_DROP: 4},
        skipped={},
    )
    spans = {AnomalyType.VOLUME_SPIKE: ((START, START), (START, START))}
    rows, why = calibration_rows(refusal=None, trace=trace, direction="both", spans=spans)
    assert why is None
    by_type = {row.anomaly_type: row for row in rows}
    assert [row.anomaly_type for row in rows[:3]] == [
        AnomalyType.NO_EVENTS,
        AnomalyType.VOLUME_DROP,
        AnomalyType.VOLUME_SPIKE,
    ]
    spike = by_type[AnomalyType.VOLUME_SPIKE]
    assert spike.replayed_flags == 2
    assert spike.noise_flags == flags_on_noise((START, second), spans[AnomalyType.VOLUME_SPIKE])
    assert spike.noise_flags == 1
    drop = by_type[AnomalyType.VOLUME_DROP]
    assert drop.replayed_flags == 0 and drop.noise_flags == 0
    silent = by_type[AnomalyType.PARSING_DEGRADATION]
    assert silent.replayed_flags is None and silent.noise_flags is None
    assert silent.reason is not None
    assert silent.reason.code == "noise.preview.not_replayable"


def test_a_refused_replay_nulls_every_calibration_count() -> None:
    from app.noise.backtest import reason

    why = reason("noise.preview.maintenance")
    rows, returned = calibration_rows(refusal=why, trace=None, direction="spike", spans={})
    assert returned is why
    assert rows
    assert all(row.replayed_flags is None and row.noise_flags is None for row in rows)
    assert all(row.excluded_by_direction is False for row in rows)
    assert all(
        row.reason is not None and row.reason.code == "noise.preview.maintenance" for row in rows
    )


def test_direction_exclusion_is_not_a_measured_zero() -> None:
    trace = ReplayTrace(
        flags={AnomalyType.VOLUME_DROP: (START,)},
        judged={AnomalyType.VOLUME_DROP: 2, AnomalyType.VOLUME_SPIKE: 2},
        skipped={},
    )
    rows, _why = calibration_rows(refusal=None, trace=trace, direction="spike", spans={})
    by_type = {row.anomaly_type: row for row in rows}
    drop = by_type[AnomalyType.VOLUME_DROP]
    assert drop.excluded_by_direction is True
    assert drop.replayed_flags is None and drop.noise_flags is None
    assert drop.reason is not None
    assert drop.reason.code == "noise.preview.direction_disabled"


def test_replay_trace_flags_are_anomalous_buckets_not_open_transitions() -> None:
    calm = _bucket(START, 50.0)
    one = _bucket(START + timedelta(minutes=5), 200.0)
    two = _bucket(START + timedelta(minutes=10), 200.0)
    thresholds = Thresholds()
    by_type = dict.fromkeys(AnomalyType, thresholds)
    single = [calm, one]
    trace = replay_trace(single, thresholds=thresholds)
    opened, _judged, _skipped = fold_buckets(single, thresholds=thresholds, by_type=by_type)
    assert trace.flags[AnomalyType.VOLUME_SPIKE] == (one.start,)
    assert opened.get(AnomalyType.VOLUME_SPIKE, 0) == 0
    pair = [calm, one, two]
    before = [bucket.point.average_eps for bucket in pair]
    traced = replay_trace(pair, thresholds=thresholds)
    opened_pair, _judged, _skipped = fold_buckets(pair, thresholds=thresholds, by_type=by_type)
    assert traced.flags[AnomalyType.VOLUME_SPIKE] == (one.start, two.start)
    assert opened_pair.get(AnomalyType.VOLUME_SPIKE, 0) == 1
    assert [bucket.point.average_eps for bucket in pair] == before


def test_overall_state_order() -> None:
    ok = _build()
    state, why = overall_state(
        monitored=True, has_cells=True, has_reliable=True, cells=[ok],
        covered_days=28, window_days=28, not_judged_buckets=0, total_buckets=10,
    )
    assert state is ModelState.OK and why is None
    state, why = overall_state(
        monitored=False, has_cells=True, has_reliable=True, cells=[ok],
        covered_days=2, window_days=28, not_judged_buckets=0, total_buckets=10,
    )
    assert state is ModelState.NOT_JUDGED
    assert why is not None and why.code == "noise.model.not_monitored"
    state, why = overall_state(
        monitored=True, has_cells=False, has_reliable=False, cells=[],
        covered_days=0, window_days=28, not_judged_buckets=0, total_buckets=0,
    )
    assert why is not None and why.code == "noise.model.no_baseline"
    state, why = overall_state(
        monitored=True, has_cells=True, has_reliable=False, cells=[ok],
        covered_days=28, window_days=28, not_judged_buckets=0, total_buckets=10,
    )
    assert why is not None and why.code == "noise.model.not_judged"
    state, why = overall_state(
        monitored=True, has_cells=True, has_reliable=True, cells=[ok],
        covered_days=2, window_days=28, not_judged_buckets=50, total_buckets=100,
    )
    assert why is not None and why.code == "noise.model.buckets_not_judged"
    state, why = overall_state(
        monitored=True, has_cells=True, has_reliable=True, cells=[ok],
        covered_days=3, window_days=28, not_judged_buckets=0, total_buckets=10,
    )
    assert why is not None and why.code == "noise.model.insufficient_coverage"
    partial = _build(covered_days=14)
    state, why = overall_state(
        monitored=True, has_cells=True, has_reliable=True, cells=[ok, partial],
        covered_days=14, window_days=28, not_judged_buckets=0, total_buckets=10,
    )
    assert state is ModelState.PARTIAL
    assert why is not None and why.code == "noise.model.partial"


def test_model_route_without_a_token_is_401() -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app.dependency_overrides[get_settings] = lambda: settings

    async def _no_session():  # type: ignore[no-untyped-def]
        yield None

    app.dependency_overrides[get_session] = _no_session
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/api/v1/noise/sources/{uuid.uuid4()}/model")
    assert response.status_code == 401


def test_disabled_preview_is_a_404_and_does_not_read() -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"low_volume_model_preview_enabled": False})
    app.dependency_overrides[get_settings] = lambda: settings

    async def _session():  # type: ignore[no-untyped-def]
        yield object()

    app.dependency_overrides[get_session] = _session
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/api/v1/noise/sources/{uuid.uuid4()}/model")
    assert response.status_code == 404
    assert response.json()["detail"] == "low-volume model preview is not enabled"
