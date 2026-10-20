"""The history estimate is the planner's own arithmetic (PW-13d), pure."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.models.enums import PwHistoryState, PwScopeKind
from app.property_watch.estimate import ASSUMPTIONS, estimate_history, simulate_reading
from app.services import property_watches as svc

SETTINGS = Settings()

#: PW-13b's simulated figures (docs/tasks/PW-13b.report.md §3): (days, rho) -> (searches, ticks)
#: with one pin, 12 history searches per tick, 5-minute buckets. §H.4.5's table is optimistic.
PW13B = {
    (1, 6): (3, 1),
    (7, 6): (11, 2),
    (28, 6): (29, 5),
    (1, 200): (30, 3),
    (7, 200): (161, 14),
    (28, 200): (617, 52),
}


@pytest.mark.parametrize(("days", "rho"), list(PW13B))
def test_the_planner_arithmetic_reproduces_pw13bs_simulated_figures(days: int, rho: int) -> None:
    got = simulate_reading(
        days=days, rows_per_bucket=rho, pinned_rows_per_bucket=1,
        settings=SETTINGS, cadence_seconds=300,
    )
    assert got is not None
    assert (got.searches, got.ticks) == PW13B[(days, rho)]


@pytest.mark.parametrize(
    ("days", "rho", "searches", "ticks"),
    [(1, 6, 2, 1), (7, 6, 9, 2), (28, 6, 24, 5), (1, 200, 28, 3), (7, 200, 147, 13),
     (28, 200, 561, 47)],
)
def test_without_pins_there_is_no_pinned_search(
    days: int, rho: int, searches: int, ticks: int
) -> None:
    got = simulate_reading(
        days=days, rows_per_bucket=rho, pinned_rows_per_bucket=0,
        settings=SETTINGS, cadence_seconds=300,
    )
    assert got is not None and (got.searches, got.ticks) == (searches, ticks)


def test_the_estimate_is_the_low_and_the_high_density() -> None:
    # one pinned value: the low density is 1 row per bucket, the high one the 200-value ceiling
    found = estimate_history(
        days=28, pinned_rows_per_bucket=1, settings=SETTINGS, cadence_seconds=300
    )
    assert found is not None
    assert (found.searches_min, found.searches_max) == (20, 617)
    assert (found.ticks_min, found.ticks_max) == (5, 52)
    assert found.cadence_seconds == 300 and found.assumptions == ASSUMPTIONS
    # no pins: the low density is still one row per bucket, and nothing is reserved for PINNED
    bare = estimate_history(
        days=28, pinned_rows_per_bucket=0, settings=SETTINGS, cadence_seconds=300
    )
    assert bare is not None and (bare.searches_min, bare.searches_max) == (15, 561)


def test_the_high_density_is_the_active_value_ceiling_never_below_the_low_one() -> None:
    small = SETTINGS.model_copy(update={"pw_max_active_values_per_watch": 6})
    found = estimate_history(days=7, pinned_rows_per_bucket=0, settings=small, cadence_seconds=300)
    solo = simulate_reading(
        days=7, rows_per_bucket=6, pinned_rows_per_bucket=0, settings=small, cadence_seconds=300
    )
    assert found is not None and solo is not None
    assert found.searches_max == solo.searches and found.ticks_max == solo.ticks


def test_a_slower_cadence_moves_the_boundary_further_each_tick() -> None:
    slow = estimate_history(
        days=28, pinned_rows_per_bucket=1, settings=SETTINGS, cadence_seconds=3600
    )
    assert slow is not None and slow.cadence_seconds == 3600
    assert slow.searches_max > 617  # a longer window to catch up before the hand-off


def test_a_bucket_that_alone_overflows_a_search_has_no_estimate() -> None:
    tight = SETTINGS.model_copy(update={"pw_max_rows_per_search": 100})
    assert estimate_history(
        days=1, pinned_rows_per_bucket=500, settings=tight, cadence_seconds=300
    ) is None


def test_the_budget_per_tick_is_the_bootstrap_setting() -> None:
    wide = SETTINGS.model_copy(update={"pw_bootstrap_searches_per_tick": 60})
    got = simulate_reading(
        days=28, rows_per_bucket=200, pinned_rows_per_bucket=1,
        settings=wide, cadence_seconds=300,
    )
    base = simulate_reading(
        days=28, rows_per_bucket=200, pinned_rows_per_bucket=1,
        settings=SETTINGS, cadence_seconds=300,
    )
    assert got is not None and base is not None and got.ticks < base.ticks


@pytest.mark.parametrize(("days", "rho", "pins"), [(0, 1, 0), (1, 0, 0), (1, 1, -1)])
def test_a_reading_needs_days_and_density(days: int, rho: int, pins: int) -> None:
    with pytest.raises(ValueError):
        simulate_reading(
            days=days, rows_per_bucket=rho, pinned_rows_per_bucket=pins,
            settings=SETTINGS, cadence_seconds=300,
        )


# --------------------------------------------------------------- the service's gate
def _watch(**kw: Any) -> Any:
    values: dict[str, Any] = dict(
        history_days=28,
        history_state=PwHistoryState.NOT_REQUESTED,
        pinned_values=[],
        properties=[{"kind": "NORMALIZED", "name": "username"}],
        scope_kind=PwScopeKind.LOG_SOURCE_TYPE,
    )
    values.update(kw)
    return SimpleNamespace(**values)


def test_days_zero_has_no_estimate() -> None:
    assert svc.history_estimate(_watch(history_days=0), SETTINGS, cadence_seconds=300) is None
    assert svc.history_estimate(_watch(history_days=None), SETTINGS, cadence_seconds=300) is None


@pytest.mark.parametrize(
    "state",
    [PwHistoryState.COMPLETE, PwHistoryState.PARTIAL, PwHistoryState.UNAVAILABLE],
)
def test_a_finished_reading_has_no_estimate(state: PwHistoryState) -> None:
    assert svc.history_estimate(_watch(history_state=state), SETTINGS, cadence_seconds=300) is None


@pytest.mark.parametrize("state", [PwHistoryState.NOT_REQUESTED, PwHistoryState.READING])
def test_a_watch_that_has_asked_and_not_finished_has_one(state: PwHistoryState) -> None:
    out = svc.history_estimate(_watch(history_state=state), SETTINGS, cadence_seconds=300)
    assert out is not None and out.cadence_seconds == 300
    assert (out.searches_min, out.searches_max) == (15, 561)


def test_pins_enter_the_estimate_through_the_pinned_rows() -> None:
    watch = _watch(pinned_values=[["alice"], ["bob"], ["carol"]])
    out = svc.history_estimate(watch, SETTINGS, cadence_seconds=300)
    assert out is not None and (out.searches_min, out.searches_max) == (30, 617)


def test_pinned_rows_is_the_product_of_the_distinct_components() -> None:
    two = _watch(
        properties=[{"kind": "NORMALIZED", "name": "username"},
                    {"kind": "CUSTOM_PROPERTY", "name": "WAF_Action"}],
        pinned_values=[["a", "x"], ["a", "y"], ["b", "x"]],
    )
    assert svc.pinned_rows(two, SETTINGS) == 4  # {a, b} x {x, y}
    assert svc.pinned_rows(_watch(), SETTINGS) == 0


def test_days_read_counts_whole_buckets_from_the_floor_through_the_last_bucket() -> None:
    floor = datetime(2026, 10, 1, tzinfo=UTC)
    one_day_of_buckets = datetime(2026, 10, 1, 23, 55, tzinfo=UTC)
    assert svc.days_read(floor, one_day_of_buckets, 300) == 1.0
    assert svc.days_read(floor, floor, 300) == 0.0  # one bucket: 0.0 days, a measured value
    assert svc.days_read(None, one_day_of_buckets, 300) is None
    assert svc.days_read(floor, None, 300) is None
    assert svc.days_read(one_day_of_buckets, floor, 300) is None
