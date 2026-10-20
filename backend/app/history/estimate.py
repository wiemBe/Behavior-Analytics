"""The static cost model of the history backfill (HISTORY-BACKFILL §B.8.2, §B.8.3, §B.10.2).

Pure: counts in, searches, ticks and hours out. No database, no clock, no settings object;
the caller passes a `PlannerSettings` per family (its bucket size, row cap, cadence and the
window limit), so each family's own row cap and cadence are the caller's facts.

Every figure is an estimate of the design model, never a measurement and never an ETA. The
searches of a family are a function of the days (`*_searches(..., days, ...)`), which is what
`fit_window` needs to find the largest window that fits the budget (§B.6.5).

The formulas, with `rho` the rows one bucket returns and `target` the rows one search may
return (80 % of the family's row cap):

* a search spans `min(max_span_buckets, floor(target / rho))` buckets, so a dimension costs
  `ceil(buckets / that)` searches (`searches_for`). The span is also capped at 168 hours,
  the planner's hard limit: a search never covers more than a week, whatever the rows.
* Phase A: `rho` is the number of monitored sources;
* WAF, per source: the volume over the whole window, plus action and response code
  (`rho x 2` each) and source IP (`rho x 5`) over the breakdown days only;
* IPS, per group of sources: TOTALS (`rho` = sources), destination port, destination IP,
  signature and source IP (values summed over the group), action (3 per source) and port
  fan-out (10 per source);
* catch-all: `rho` is the senders per bucket, under the catch-all's own row cap;
* rule matches: hourly windows, `rho` the enabled rules, plus one console total.

A dimension with no value at all still costs the searches to prove it is empty: the model
never reports a dimension as free.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.history.planner import (
    PlannerSettings,
    fit_window,
    searches_for,
    window_buckets,
)

#: WAF breakdowns over the breakdown days: action and response code (x2), source IP (x5).
WAF_BREAKDOWN_FACTORS: tuple[int, ...] = (2, 2, 5)
#: IPS: the action dimension returns up to three values per source (blocked, allowed, other).
IPS_ACTION_VALUES_PER_SOURCE = 3
#: IPS: the port fan-out dimension returns up to ten rows per source and bucket.
IPS_FANOUT_ROWS_PER_SOURCE = 10
#: IPS: source IPs per source and bucket, when the model has no count of them.
IPS_SOURCE_IPS_PER_SOURCE = 50
#: Rule matches add one console-wide total search to the per-rule windows.
RULE_MATCH_TOTAL_SEARCHES = 1


@dataclass(frozen=True)
class IpsGroup:
    """Rows per bucket of one group of IPS sources that share a plan (values summed)."""

    sources: int
    ports: int
    ips: int
    signatures: int
    source_ips: int

    @classmethod
    def even(
        cls,
        sources: int,
        *,
        ports: int = 20,
        ips: int = 40,
        signatures: int = 40,
        source_ips: int = IPS_SOURCE_IPS_PER_SOURCE,
    ) -> IpsGroup:
        """`sources` sources with the same values each (the shapes of §B.8.1)."""
        return cls(
            sources,
            sources * ports,
            sources * ips,
            sources * signatures,
            sources * source_ips,
        )


# --------------------------------------------------------------------------- searches


def phase_a_searches(sources: int, days: int, ps: PlannerSettings) -> int:
    """`ceil(buckets / min(2016, floor(target / sources)))`."""
    return searches_for(window_buckets(days, ps), sources, ps)


def waf_source_searches(endpoints: int, days: int, breakdown_days: int, ps: PlannerSettings) -> int:
    """One WAF source: the volume over `days`, three breakdowns over `breakdown_days`."""
    volume = searches_for(window_buckets(days, ps), endpoints, ps)
    breakdown_buckets = min(breakdown_days, days) * 86400 // ps.bucket_seconds
    return volume + sum(
        searches_for(breakdown_buckets, endpoints * factor, ps) for factor in WAF_BREAKDOWN_FACTORS
    )


def waf_searches(
    endpoints_per_source: Sequence[int], days: int, breakdown_days: int, ps: PlannerSettings
) -> int:
    return sum(waf_source_searches(n, days, breakdown_days, ps) for n in endpoints_per_source)


def ips_group_searches(group: IpsGroup, days: int, ps: PlannerSettings) -> int:
    """The seven dimensions of §B.8.2 for one group."""
    buckets = window_buckets(days, ps)
    rows_per_bucket = (
        group.sources,
        group.ports,
        group.ips,
        group.signatures,
        group.source_ips,
        group.sources * IPS_ACTION_VALUES_PER_SOURCE,
        group.sources * IPS_FANOUT_ROWS_PER_SOURCE,
    )
    return sum(searches_for(buckets, rho, ps) for rho in rows_per_bucket)


def ips_searches(groups: Sequence[IpsGroup], days: int, ps: PlannerSettings) -> int:
    return sum(ips_group_searches(group, days, ps) for group in groups)


def catchall_searches(senders: int, days: int, ps: PlannerSettings) -> int:
    """`ceil(buckets / floor(target / senders))`, `ps.row_cap` being the catch-all's own."""
    return searches_for(window_buckets(days, ps), senders, ps)


def rule_match_searches(rules: int, days: int, ps: PlannerSettings) -> int:
    """Hourly windows (`ps.bucket_seconds` = the window), one row per matching rule."""
    return searches_for(window_buckets(days, ps), rules, ps) + RULE_MATCH_TOTAL_SEARCHES


# --------------------------------------------------------------------------- the estimate


@dataclass(frozen=True)
class FamilyEstimate:
    """One family's estimate. `searches`, `ticks` and `hours` are None when it has none."""

    family: str
    searches: int | None
    ticks: int | None
    hours: float | None
    days_requested: int
    days_fitted: int | None
    reduced: bool
    refused: bool
    reason: str
    assumptions: tuple[str, ...]


def estimate_family(
    family: str,
    searches_for_days: Callable[[int], int],
    *,
    days_requested: int,
    end: datetime,
    ps: PlannerSettings,
    searches_per_tick: int,
    assumptions: Sequence[str],
) -> FamilyEstimate:
    """Fit the window to the budget with the core's `fit_window` (§B.6.5), at the cadence."""
    fit = fit_window(
        days_requested,
        searches_for_days,
        end=end,
        settings=ps,
        searches_per_tick=searches_per_tick,
    )
    return FamilyEstimate(
        family=family,
        searches=fit.searches,
        ticks=fit.ticks,
        hours=fit.hours,
        days_requested=days_requested,
        days_fitted=fit.days_fitted,
        reduced=fit.reduced,
        refused=fit.refused,
        reason=fit.reason,
        assumptions=tuple(assumptions),
    )


def not_estimable(
    family: str,
    *,
    days_requested: int,
    reason: str,
    assumptions: Sequence[str],
    refused: bool = False,
) -> FamilyEstimate:
    """No figure: the count the model needs is missing, or the family has no window.

    Fail closed (rule 8): every number is None, never zero.
    """
    return FamilyEstimate(
        family=family,
        searches=None,
        ticks=None,
        hours=None,
        days_requested=days_requested,
        days_fitted=None,
        reduced=False,
        refused=refused,
        reason=reason,
        assumptions=tuple(assumptions),
    )
