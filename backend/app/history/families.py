"""Which tables, switches and limits each history family has (HISTORY-BACKFILL §B.3.1, §B.6.1).

A registry of plain facts plus two pure functions of a settings object:

* `fit_family_window` - the days a family may read, and what limited them. The window is
  the smallest of `history_backfill_days`, the family's own lookback and the largest `d`
  with `d x 24 + history_backfill_max_hours < retention x 24`. Below seven days there is
  no verdict-worthy history (§B.6.5) and the family is unavailable, with a reason in words.
* `history_search_budget` - the history sub-budget under the tick's search cap (§B.6.1).

Settings are named by attribute name and read with `getattr` from whatever object the
caller passes; nothing in `app.history` imports `app.core.config`. The window is fitted at
run time and never refused at startup (the families are released, and an old configuration
must keep starting).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.history.planner import DEFAULT_MIN_WINDOW_DAYS
from app.history.state import SCOPE_TABLE_FAMILIES, HistoryFamily

DAYS_SETTING = "history_backfill_days"
MAX_HOURS_SETTING = "history_backfill_max_hours"
PER_TICK_SETTING = "history_backfill_searches_per_tick"
CADENCE_CAP_SETTING = "search_cadence_max_searches_per_tick"


@dataclass(frozen=True)
class FamilySpec:
    """The facts about one family that the foundations and the adapters share."""

    family: HistoryFamily
    #: Tables whose rows carry the marker (`origin`, or `collection_source` for Phase A).
    tables: tuple[str, ...]
    #: Setting that switches the family's collection on, or None with `always_on_reason`.
    collection_switch: str | None
    always_on_reason: str | None
    #: The family's own lookback in days, or None when it has none.
    lookback_setting: str | None
    #: Retention in days, and the setting that switches that retention on.
    retention_setting: str | None
    retention_switch: str | None
    #: `SearchFamily` member name the family's searches run under.
    search_family: str
    #: Words on retention, for the admin page and for review.
    retention_note: str


FAMILY_SPECS: dict[HistoryFamily, FamilySpec] = {
    HistoryFamily.PHASE_A: FamilySpec(
        family=HistoryFamily.PHASE_A,
        tables=("log_source_metric",),
        collection_switch=None,
        always_on_reason="source-volume collection is the platform's base collection",
        lookback_setting="baseline_lookback_days",
        retention_setting="retention_log_source_metric_days",
        retention_switch="retention_enabled",
        search_family="PHASE_A_VOLUME",
        retention_note="off by default; applies only when enabled and a number of days is set",
    ),
    HistoryFamily.WAF: FamilySpec(
        family=HistoryFamily.WAF,
        tables=("waf_source_bucket",),
        collection_switch="waf_collection_enabled",
        always_on_reason=None,
        lookback_setting="baseline_lookback_days",
        retention_setting="waf_metric_retention_days",
        retention_switch="waf_retention_enabled",
        search_family="WAF_COLLECTION",
        retention_note="WAF has no lookback of its own; it reads the baseline lookback",
    ),
    HistoryFamily.IPS: FamilySpec(
        family=HistoryFamily.IPS,
        tables=("ips_source_bucket",),
        collection_switch="ips_collection_enabled",
        always_on_reason=None,
        lookback_setting="ips_baseline_lookback_days",
        retention_setting="ips_metric_retention_days",
        retention_switch="ips_retention_enabled",
        search_family="IPS_COLLECTION",
        retention_note="applies while ips_retention_enabled is on",
    ),
    HistoryFamily.CATCHALL: FamilySpec(
        family=HistoryFamily.CATCHALL,
        tables=("catchall_bucket",),
        collection_switch="catchall_collection_enabled",
        always_on_reason=None,
        lookback_setting=None,
        retention_setting="catchall_retention_days",
        retention_switch=None,
        search_family="CATCHALL_SENDERS",
        retention_note="the retention task has no switch; senders have no lookback",
    ),
    HistoryFamily.RULE_MATCH: FamilySpec(
        family=HistoryFamily.RULE_MATCH,
        tables=("rule_match_window",),
        collection_switch="rule_match_collection_enabled",
        always_on_reason=None,
        lookback_setting="rule_health_window_days",
        retention_setting="rule_match_retention_days",
        retention_switch=None,
        search_family="RULE_MATCH",
        retention_note="the retention task has no switch; the family reads the rule-health window",
    ),
}


def family_spec(family: HistoryFamily) -> FamilySpec:
    """The registry entry of a family that has a scope row (not a property watch)."""
    try:
        return FAMILY_SPECS[family]
    except KeyError:
        raise KeyError(f"{family.value} has no scope-table entry in the family registry") from None


# --------------------------------------------------------------------------- window fitting


@dataclass(frozen=True)
class FittedWindow:
    """The outcome of `fit_family_window`.

    `days` is None when the family is unavailable. `limited_by` names the setting(s) that
    set the smallest bound (all of them on a tie). `reason` is words for the scope row's
    `reason` column and the admin page: it names settings and their values, and says
    nothing when the full requested window fits.
    """

    family: HistoryFamily
    requested_days: int
    days: int | None
    limited_by: tuple[str, ...]
    reason: str | None

    @property
    def available(self) -> bool:
        return self.days is not None


def _label(setting: str) -> str:
    return setting.upper()


def _retention_applies(spec: FamilySpec, settings: Any) -> int | None:
    """The retention in days that applies to the family, or None when none does."""
    if spec.retention_setting is None:
        return None
    if spec.retention_switch is not None and not getattr(settings, spec.retention_switch):
        return None
    days = getattr(settings, spec.retention_setting)
    return None if days is None else int(days)


def _largest_window_below(retention_days: int, max_hours: int) -> int:
    """Largest `d` with `d x 24 + max_hours < retention_days x 24`."""
    return (retention_days * 24 - max_hours - 1) // 24


def fit_family_window(family: HistoryFamily, settings: Any) -> FittedWindow:
    """The effective window of a family, or why it has none.

    Fail closed (repository rule 8): a window below seven days is `UNAVAILABLE` upstream,
    never a short window read as if it were enough.
    """
    spec = family_spec(family)
    requested = int(getattr(settings, DAYS_SETTING))
    max_hours = int(getattr(settings, MAX_HOURS_SETTING))
    candidates: list[tuple[str, int]] = [(DAYS_SETTING, requested)]
    if spec.lookback_setting is not None:
        candidates.append((spec.lookback_setting, int(getattr(settings, spec.lookback_setting))))
    retention = _retention_applies(spec, settings)
    retention_fit: int | None = None
    if retention is not None and spec.retention_setting is not None:
        retention_fit = _largest_window_below(retention, max_hours)
        candidates.append((spec.retention_setting, retention_fit))

    smallest = min(value for _, value in candidates)
    limited_by = tuple(name for name, value in candidates if value == smallest)

    if smallest < DEFAULT_MIN_WINDOW_DAYS:
        return FittedWindow(
            family=family,
            requested_days=requested,
            days=None,
            limited_by=limited_by,
            reason=_unavailable_reason(
                spec, smallest, limited_by, settings, retention, retention_fit, max_hours
            ),
        )
    if smallest < requested:
        return FittedWindow(
            family=family,
            requested_days=requested,
            days=smallest,
            limited_by=limited_by,
            reason=(
                f"window reduced from {requested} to {smallest} days: "
                + _limit_words(spec, limited_by, settings, retention, max_hours)
            ),
        )
    return FittedWindow(
        family=family, requested_days=requested, days=smallest, limited_by=limited_by, reason=None
    )


def _limit_words(
    spec: FamilySpec,
    limited_by: tuple[str, ...],
    settings: Any,
    retention: int | None,
    max_hours: int,
) -> str:
    parts: list[str] = []
    for name in limited_by:
        if name == spec.retention_setting and retention is not None:
            parts.append(
                f"{_label(name)} is {retention} days and {_label(MAX_HOURS_SETTING)} is "
                f"{max_hours} hours, so the reading would outlive the retention"
            )
        else:
            parts.append(f"{_label(name)} is {getattr(settings, name)} days")
    return " and ".join(parts)


def _unavailable_reason(
    spec: FamilySpec,
    smallest: int,
    limited_by: tuple[str, ...],
    settings: Any,
    retention: int | None,
    retention_fit: int | None,
    max_hours: int,
) -> str:
    fits = max(smallest, 0)
    return (
        f"history unavailable: the window would be {fits} days, below the "
        f"{DEFAULT_MIN_WINDOW_DAYS}-day minimum; "
        + _limit_words(spec, limited_by, settings, retention, max_hours)
    )


# --------------------------------------------------------------------------- search budget


def history_search_budget(settings: Any, used: int) -> int:
    """Searches a history step may start after the live pass used `used` of the tick cap.

    `max(0, min(history_backfill_searches_per_tick, search_cadence_max_searches_per_tick -
    used))` (§B.6.1): history spends only what the live pass left.
    """
    if used < 0:
        raise ValueError("used searches cannot be negative")
    cap = int(getattr(settings, CADENCE_CAP_SETTING))
    return max(0, min(int(getattr(settings, PER_TICK_SETTING)), cap - used))


# A new family in the scope table must get an entry here, or this module fails at import.
_missing = SCOPE_TABLE_FAMILIES - set(FAMILY_SPECS)
if _missing:  # pragma: no cover - a guard for a future edit
    raise RuntimeError(f"families without a registry entry: {sorted(f.value for f in _missing)}")
