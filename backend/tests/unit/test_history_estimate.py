"""The static cost model (HISTORY-BACKFILL §B.8.2, §B.8.3): the design's table, reproduced."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.history.estimate import (
    IPS_ACTION_VALUES_PER_SOURCE,
    IPS_FANOUT_ROWS_PER_SOURCE,
    WAF_BREAKDOWN_FACTORS,
    IpsGroup,
    catchall_searches,
    estimate_family,
    estimate_source_family,
    ips_group_searches,
    ips_searches,
    not_estimable,
    phase_a_searches,
    rule_match_searches,
    waf_searches,
    waf_source_searches,
)
from app.history.planner import PlannerSettings, searches_for, ticks_for, window_buckets

END = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
#: §B.8.1: rows per search 4 000 of a 5 000 cap; catch-all 1 600 of its 2 000; hourly windows.
PS = PlannerSettings(row_cap=5000)
PS_CATCHALL = PlannerSettings(row_cap=2000)
PS_RULES = PlannerSettings(bucket_seconds=3600, row_cap=5000)

# Shape -> (Phase A sources, endpoints of each WAF source, IPS sources, senders, rules/hour)
SHAPES = {
    "small": (20, [30], 1, 20, 30),
    "typical": (100, [100] * 3, 5, 150, 80),
    "large": (1000, [300] * 20, 30, 800, 150),
}

# §B.8.2, row by row. IPS TOTALS for the typical shape is the one number that does not follow
# its formula: the design prints 5, `ceil(8352 / min(2016, floor(4000 / 5)))` is 11 (the small
# and large shapes, 5 and 63, do follow it). The formula's value is pinned below.
DESIGN = {
    "phase_a": {"small": 42, "typical": 209, "large": 2088},
    "waf_volume": {"small": 63, "typical": 209, "large": 643},
    "waf_breakdowns": {"small": (31, 31, 78), "typical": (101, 101, 252),
                       "large": (336, 336, 1008)},
    "waf_all": {"small": 203, "typical": 1989, "large": 46460},
    "ips_dims": {
        "small": (5, 42, 84, 84, 105, 7, 21),
        "typical": (5, 209, 418, 418, 522, 32, 105),
        "large": (63, 1392, 2784, 2784, 4176, 190, 643),
    },
    "ips_all": {"small": 348, "typical": 1709, "large": 12032},
    "catchall": {"small": 105, "typical": 836, "large": 4176},
    "rule_matches": {"small": 7, "typical": 16, "large": 30},
}
#: The pinned formula values where the design's number does not follow its own formula.
PINNED_IPS_TOTALS_TYPICAL = 11
PINNED_IPS_ALL_TYPICAL = 1715
#: The design's "All families" row (703 / 4 759 / 65 785) is not the sum of its own rows
#: (705 / 4 759 with TOTALS 5 / 64 786): pinned as the sums of the formula values.
PINNED_ALL = {"small": 705, "typical": 4765, "large": 64786}


def ips_dimension_searches(n: int, ps: PlannerSettings = PS) -> tuple[int, ...]:
    """The seven IPS dimensions one by one, as the design's row lists them."""
    buckets = window_buckets(28, ps)
    group = IpsGroup.even(n)
    rows = (
        n,
        group.ports,
        group.ips,
        group.signatures,
        group.source_ips,
        n * IPS_ACTION_VALUES_PER_SOURCE,
        n * IPS_FANOUT_ROWS_PER_SOURCE,
    )
    return tuple(searches_for(buckets, rho, ps) for rho in rows)


def test_the_window_is_8352_buckets_and_744_hourly_windows() -> None:
    assert window_buckets(28, PS) == 8352
    assert window_buckets(30, PS_RULES) == 744


@pytest.mark.parametrize("shape", SHAPES)
def test_phase_a_searches(shape: str) -> None:
    assert phase_a_searches(SHAPES[shape][0], 28, PS) == DESIGN["phase_a"][shape]


@pytest.mark.parametrize("shape", SHAPES)
def test_waf_per_source_volume_breakdowns_and_total(shape: str) -> None:
    endpoints = SHAPES[shape][1]
    rho = endpoints[0]

    assert searches_for(window_buckets(28, PS), rho, PS) == DESIGN["waf_volume"][shape]
    breakdown_buckets = 7 * 288
    assert breakdown_buckets == 2016
    assert tuple(
        searches_for(breakdown_buckets, rho * factor, PS) for factor in WAF_BREAKDOWN_FACTORS
    ) == DESIGN["waf_breakdowns"][shape]
    assert waf_searches(endpoints, 28, 7, PS) == DESIGN["waf_all"][shape]
    assert waf_source_searches(rho, 28, 7, PS) * len(endpoints) == DESIGN["waf_all"][shape]


@pytest.mark.parametrize("shape", SHAPES)
def test_ips_dimensions_and_total(shape: str) -> None:
    n = SHAPES[shape][2]
    got = ips_dimension_searches(n)
    expected = list(DESIGN["ips_dims"][shape])
    if shape == "typical":
        assert expected[0] == 5  # the design's number, which does not follow its formula
        expected[0] = PINNED_IPS_TOTALS_TYPICAL
    assert got == tuple(expected)
    assert ips_group_searches(IpsGroup.even(n), 28, PS) == (
        PINNED_IPS_ALL_TYPICAL if shape == "typical" else DESIGN["ips_all"][shape]
    )
    assert ips_searches([IpsGroup.even(n)], 28, PS) == ips_group_searches(IpsGroup.even(n), 28, PS)


@pytest.mark.parametrize("shape", SHAPES)
def test_catchall_searches_under_its_own_row_cap(shape: str) -> None:
    assert catchall_searches(SHAPES[shape][3], 28, PS_CATCHALL) == DESIGN["catchall"][shape]


@pytest.mark.parametrize("shape", SHAPES)
def test_rule_match_searches_are_hourly_windows_plus_one_total(shape: str) -> None:
    assert rule_match_searches(SHAPES[shape][4], 30, PS_RULES) == DESIGN["rule_matches"][shape]


@pytest.mark.parametrize("shape", SHAPES)
def test_all_families_are_the_sum_of_the_rows(shape: str) -> None:
    pa, waf, ips, senders, rules = SHAPES[shape]
    total = (
        phase_a_searches(pa, 28, PS)
        + waf_searches(waf, 28, 7, PS)
        + ips_group_searches(IpsGroup.even(ips), 28, PS)
        + catchall_searches(senders, 28, PS_CATCHALL)
        + rule_match_searches(rules, 30, PS_RULES)
    )
    assert total == PINNED_ALL[shape]


def test_the_typical_ips_ticks_follow_the_formula_not_the_design_row() -> None:
    """The design's tick table is the sum of its own search row (1 709); the formula says 1 715."""
    assert ticks_for(DESIGN["ips_all"]["typical"], 10) == 171
    assert ticks_for(PINNED_IPS_ALL_TYPICAL, 10) == 172


def test_the_rule_match_row_cap_label_does_not_follow_its_own_formula() -> None:
    """§B.8.2's rule-match row says "row cap 4 000" and divides by 4 000.

    The divisor of 4 000 is the 80 % target of the 5 000 cap (§B.6.2's
    `ariel_multi_bucket_max_rows`), not the target of a 4 000 cap, whose target
    would be 3 200. The design's numbers (7/16/30) follow the divisor; a 4 000 cap
    would give 9/20/37. The formula's value is pinned here.
    """
    ps_4000 = PlannerSettings(bucket_seconds=3600, row_cap=4000)
    assert ps_4000.row_target_rows == 3200
    assert rule_match_searches(30, 30, ps_4000) == 9
    assert rule_match_searches(80, 30, ps_4000) == 20
    assert rule_match_searches(150, 30, ps_4000) == 37
    # The table's own numbers come from the 5 000 cap's 4 000 target.
    assert PS_RULES.row_target_rows == 4000
    assert rule_match_searches(30, 30, PS_RULES) == DESIGN["rule_matches"]["small"]
    assert rule_match_searches(80, 30, PS_RULES) == DESIGN["rule_matches"]["typical"]
    assert rule_match_searches(150, 30, PS_RULES) == DESIGN["rule_matches"]["large"]


def test_the_rule_match_window_of_the_design_is_thirty_days_and_the_core_fits_28() -> None:
    """§B.8.1 assumes 30 days of rule-match windows (744); the core fits 28.

    `fit_family_window` takes the smaller of `history_backfill_days` (28) and the
    family's own lookback (`rule_health_window_days`, 30), so the window the
    collector reads is 28 days: 696 hourly windows, not 744. The table's rule-match
    numbers are therefore the ones of a 30-day window.
    """
    assert window_buckets(30, PS_RULES) == 744
    assert window_buckets(28, PS_RULES) == 696
    # The design's numbers are the 30-day ones.
    assert rule_match_searches(80, 30, PS_RULES) == DESIGN["rule_matches"]["typical"]
    assert rule_match_searches(80, 28, PS_RULES) == 15


# §B.8.3: ticks at 10 history searches per tick (WAF: 20); wall time at the 5-minute cadence.
@pytest.mark.parametrize(
    ("searches", "per_tick", "ticks", "minutes"),
    [
        (42, 10, 5, 25), (209, 10, 21, 105), (2088, 10, 209, 1045),  # Phase A
        (203, 20, 11, 55), (1989, 20, 100, 500), (46460, 20, 2323, 11615),  # WAF
        (348, 10, 35, 175), (1709, 10, 171, 855), (12032, 10, 1204, 6020),  # IPS (design)
        (105, 10, 11, 55), (836, 10, 84, 420), (4176, 10, 418, 2090),  # catch-all
        (7, 10, 1, 5), (16, 10, 2, 10), (30, 10, 3, 15),  # rule matches
    ],
)
def test_ticks_and_wall_time_of_the_design_table(
    searches: int, per_tick: int, ticks: int, minutes: int
) -> None:
    assert ticks_for(searches, per_tick) == ticks
    assert ticks * 5 == minutes


def fit(searches_for_days, days=28, *, ps=PS, per_tick=10, max_hours=None):  # type: ignore[no-untyped-def]
    ps = PlannerSettings(
        bucket_seconds=ps.bucket_seconds,
        row_cap=ps.row_cap,
        max_hours=max_hours or ps.max_hours,
        cadence_seconds=ps.cadence_seconds,
    )
    return estimate_family(
        "X", searches_for_days, days_requested=days, end=END, ps=ps,
        searches_per_tick=per_tick, assumptions=["a"],
    )


def test_a_window_that_fits_is_not_reduced() -> None:
    estimate = fit(lambda d: phase_a_searches(100, d, PS))
    assert (estimate.searches, estimate.ticks, estimate.hours) == (209, 21, 105 / 60)
    assert (estimate.days_requested, estimate.days_fitted) == (28, 28)
    assert (estimate.reduced, estimate.refused) == (False, False)
    assert estimate.assumptions == ("a",)


def test_a_large_waf_window_is_refused_because_the_breakdowns_alone_do_not_fit() -> None:
    """§B.8.3 says the Large WAF window "is fitted"; §B.8.2's formula says otherwise.

    The breakdown pass reads a fixed seven days whatever the window is, and for
    the Large shape it alone costs 33 600 searches (1 680 ticks, 140 h) over the
    72-hour limit. The formula's value is what is pinned here: even a seven-day
    window needs 1 858 ticks (154.8 h), so the window is refused, not fitted.
    """
    estimate = fit(lambda d: waf_searches([300] * 20, d, 7, PS), per_tick=20)
    assert estimate.refused and not estimate.reduced
    assert estimate.days_fitted is None
    # the figure of the smallest window, not a zero
    assert estimate.searches == 37160
    assert estimate.ticks == 1858
    assert estimate.hours is not None and estimate.hours > 72
    # The breakdown pass alone is over the limit at any window.
    breakdown = sum(
        searches_for(7 * 288, 300 * factor, PS) for factor in WAF_BREAKDOWN_FACTORS
    ) * 20
    assert breakdown == 33600
    assert ticks_for(breakdown, 20) * 5 / 60 > 72


def test_a_large_ips_window_is_fitted_to_about_20_days() -> None:
    estimate = fit(lambda d: ips_group_searches(IpsGroup.even(30), d, PS))
    assert estimate.reduced and estimate.days_fitted is not None
    assert 19 <= estimate.days_fitted <= 21  # the design: "fitted to about 20 days"


def test_a_window_that_cannot_fit_even_in_seven_days_is_refused() -> None:
    estimate = fit(lambda d: 10_000, max_hours=1)
    assert estimate.refused and estimate.days_fitted is None
    assert estimate.searches == 10_000  # the figure of the smallest window, not a zero
    assert "history is unavailable" in estimate.reason


def test_a_count_that_is_missing_gives_no_numbers_not_zeros() -> None:
    estimate = not_estimable("WAF", days_requested=28, reason="no estimate: x", assumptions=["a"])
    assert (estimate.searches, estimate.ticks, estimate.hours, estimate.days_fitted) == (
        None, None, None, None)
    assert estimate.reason == "no estimate: x" and not estimate.reduced and not estimate.refused


def test_per_source_fit_reports_the_smallest_window_and_console_total() -> None:
    ps = PlannerSettings(row_cap=5000, max_hours=24, cadence_seconds=300)
    estimate = estimate_source_family(
        "WAF",
        [
            lambda days: waf_source_searches(1, days, 7, ps),
            lambda days: waf_source_searches(50, days, 7, ps),
        ],
        days_requested=28,
        end=END,
        ps=ps,
        source_searches_per_tick=1,
        family_searches_per_tick=2,
        assumptions=["per source"],
    )

    assert estimate.days_fitted == 15
    assert estimate.reduced and not estimate.refused
    assert estimate.searches == (
        waf_source_searches(1, 28, 7, ps) + waf_source_searches(50, 15, 7, ps)
    )
    assert "window reduced from 28 to 15 days" in estimate.reason


def test_per_source_fit_refuses_the_family_only_when_every_source_is_refused() -> None:
    ps = PlannerSettings(row_cap=5000, max_hours=1, cadence_seconds=300)
    mixed = estimate_source_family(
        "IPS",
        [lambda _days: 1, lambda _days: 1000],
        days_requested=28,
        end=END,
        ps=ps,
        source_searches_per_tick=1,
        family_searches_per_tick=2,
        assumptions=[],
    )
    all_refused = estimate_source_family(
        "IPS",
        [lambda _days: 1000, lambda _days: 2000],
        days_requested=28,
        end=END,
        ps=ps,
        source_searches_per_tick=1,
        family_searches_per_tick=2,
        assumptions=[],
    )

    assert mixed.days_fitted == 28 and not mixed.refused
    assert "1 of 2 sources are unavailable" in mixed.reason
    assert all_refused.days_fitted is None and all_refused.refused
    assert "all 2 sources are unavailable" in all_refused.reason
