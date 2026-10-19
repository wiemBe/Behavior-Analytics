"""The shared pure core of the history backfill (HISTORY-BACKFILL §B.11).

`origin` says how a row is marked, `state` is the state machine, `planner` decides the
floor, the steps and the window. Nothing here does I/O; the family adapters (HB-3 to HB-7)
and the property-watch adapter (PW-13b) call it.
"""

from app.history.families import (
    FAMILY_SPECS,
    FamilySpec,
    FittedWindow,
    family_spec,
    fit_family_window,
    history_search_budget,
)
from app.history.lifecycle import (
    ActionKind,
    ConsoleMode,
    DecisionView,
    LifecycleError,
    ScopeAction,
    ScopeRowView,
    bounded_reason,
    console_mode,
    decode_scope,
    new_scope_row,
    scope_action,
    scope_progress,
    transition_row,
)
from app.history.origin import (
    PHASE_A_HISTORY_SOURCE,
    HistoryOrigin,
    is_history_clause,
    is_live_clause,
    marker_values,
    origin_of,
)
from app.history.planner import (
    PlannerSettings,
    ScopeProgress,
    fit_window,
    interpret_scan,
    new_progress,
    plan_scan,
    plan_step,
    settle_step,
)
from app.history.progress_codec import (
    PROGRESS_VERSION,
    ProgressCodecError,
    decode_progress,
    encode_progress,
)
from app.history.source_floors import (
    SourceFloorError,
    SourceFloors,
    finish_with_sources,
)
from app.history.state import (
    HistoryFamily,
    HistoryState,
    HistoryTrigger,
    IllegalTransitionError,
    next_state,
)

__all__ = [
    "FAMILY_SPECS",
    "PHASE_A_HISTORY_SOURCE",
    "PROGRESS_VERSION",
    "ActionKind",
    "ConsoleMode",
    "DecisionView",
    "FamilySpec",
    "FittedWindow",
    "HistoryFamily",
    "HistoryOrigin",
    "HistoryState",
    "HistoryTrigger",
    "IllegalTransitionError",
    "LifecycleError",
    "PlannerSettings",
    "ProgressCodecError",
    "ScopeAction",
    "ScopeProgress",
    "ScopeRowView",
    "SourceFloorError",
    "SourceFloors",
    "bounded_reason",
    "console_mode",
    "decode_progress",
    "decode_scope",
    "encode_progress",
    "family_spec",
    "finish_with_sources",
    "fit_family_window",
    "fit_window",
    "history_search_budget",
    "interpret_scan",
    "is_history_clause",
    "is_live_clause",
    "marker_values",
    "new_progress",
    "new_scope_row",
    "next_state",
    "origin_of",
    "plan_scan",
    "plan_step",
    "scope_action",
    "scope_progress",
    "settle_step",
    "transition_row",
]
