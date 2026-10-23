// One row per family and scope for the admin table (HISTORY-BACKFILL §B.10.3). A family that is
// switched off, has no source, or has not started is still a row with its reason: an absence
// would read as "nothing to say".

import type {
  HistoryConsole,
  HistoryFamily,
  HistoryGap,
  HistoryScope,
  HistoryScopeSource,
} from "@/lib/api";

import {
  NOT_READ,
  NOT_STARTED,
  SWITCHED_OFF,
  stateInfo,
  type StateInfo,
} from "./historyFormat";

export interface HistoryRow {
  key: string;
  family: string;
  scope: string;
  scopeSub: string | null;
  state: StateInfo;
  /** Days the scope asked for, and the days the family may read when that is fewer. */
  asked: { days: number | null; of: number | null };
  /** "proved" carries the floor; "per-source" has none; "none" means nothing was measured. */
  proved: { kind: "proved"; floor: string | null } | { kind: "per-source" } | { kind: "none" };
  read: { unreadable: boolean; days: number | null; target: number | null; buckets: number | null };
  searches: number | null;
  /** The data time read through, and when the scope finished; neither is "when a step ran". */
  last: { through: string | null; finished: string | null } | null;
  reason: string | null;
  gaps: HistoryGap[];
  lastErrorClass: string | null;
  lastErrorAt: string | null;
  sourcesCut: HistoryScopeSource[] | null;
  sourcesSilent: HistoryScopeSource[] | null;
  sourcesCutCount: number | null;
  sourcesSilentCount: number | null;
  laterYes: string | null;
}

function scopeRow(console: HistoryConsole, family: HistoryFamily, scope: HistoryScope): HistoryRow {
  const perSource = family.sources !== null;
  const last =
    scope.through !== null || scope.finished_at !== null
      ? { through: scope.through, finished: scope.finished_at }
      : null;
  const target = scope.days_target ?? scope.days_requested;
  return {
    key: `${console.instance_id}:${family.family}:${scope.scope_key}`,
    family: family.family,
    scope: perSource ? (scope.name ?? `Log source ${scope.scope_key}`) : "Whole console",
    scopeSub: perSource && scope.name ? `Log source ${scope.scope_key}` : null,
    state: stateInfo(scope.state),
    asked: { days: target, of: scope.days_requested },
    proved: scope.floor_proved
      ? { kind: "proved", floor: scope.floor }
      : { kind: "per-source" },
    read: {
      unreadable: scope.progress_unreadable,
      days: scope.days_read,
      target: scope.days_target,
      buckets: scope.buckets_read,
    },
    searches: scope.searches,
    last,
    reason: scope.reason,
    gaps: scope.gaps ?? [],
    lastErrorClass: scope.last_error_class,
    lastErrorAt: scope.last_error_at,
    sourcesCut: scope.sources_cut,
    sourcesSilent: scope.sources_silent,
    sourcesCutCount: scope.sources_cut_count,
    sourcesSilentCount: scope.sources_silent_count,
    laterYes: scope.later_yes,
  };
}

/** The state of a row that has no scope row: why nothing is stored for it. */
function absentState(console: HistoryConsole, family: HistoryFamily): { state: StateInfo; reason: string } {
  if (!family.collection_enabled) return { state: SWITCHED_OFF, reason: family.collection_reason };
  if (!family.window.available) {
    return {
      state: stateInfo("UNAVAILABLE"),
      reason: family.window.reason ?? "The window could not be fitted.",
    };
  }
  if (console.mode === "UNDECIDED") {
    return { state: NOT_READ, reason: "No decision is recorded for this console." };
  }
  if (console.mode === "NO") {
    return { state: NOT_READ, reason: "The answer is No." };
  }
  if (console.mode === "STOPPED") {
    return { state: NOT_READ, reason: "Reading was stopped before this started." };
  }
  return { state: NOT_STARTED, reason: "It is created at the family's next collection." };
}

function absentRow(
  console: HistoryConsole,
  family: HistoryFamily,
  scope: string,
  scopeSub: string | null,
  keyTail: string,
  laterYes: string | null,
  override?: { state: StateInfo; reason: string },
): HistoryRow {
  const { state, reason } = override ?? absentState(console, family);
  const window = family.window;
  return {
    key: `${console.instance_id}:${family.family}:${keyTail}`,
    family: family.family,
    scope,
    scopeSub,
    state,
    asked: {
      days: window.days ?? null,
      of: window.days !== null && window.days !== window.days_requested ? window.days_requested : null,
    },
    proved: { kind: "none" },
    read: { unreadable: false, days: null, target: null, buckets: null },
    searches: null,
    last: null,
    reason,
    gaps: [],
    lastErrorClass: null,
    lastErrorAt: null,
    sourcesCut: null,
    sourcesSilent: null,
    sourcesCutCount: null,
    sourcesSilentCount: null,
    laterYes,
  };
}

export function rowsOf(console: HistoryConsole): HistoryRow[] {
  const rows: HistoryRow[] = [];
  for (const family of console.families) {
    const before = rows.length;
    for (const scope of family.scopes) rows.push(scopeRow(console, family, scope));
    for (const source of family.sources ?? []) {
      let override: { state: StateInfo; reason: string } | undefined;
      if (!source.profile_enabled) {
        override = {
          state: { label: "Profile disabled", tone: "neutral" },
          reason: "The source's profile is disabled, so it is not collected.",
        };
      }
      rows.push(
        absentRow(
          console,
          family,
          source.name,
          `Log source ${source.scope_key}`,
          `source:${source.scope_key}`,
          source.later_yes,
          override,
        ),
      );
    }
    if (rows.length === before) {
      const none = family.sources !== null;
      rows.push(
        absentRow(
          console,
          family,
          none ? "No source configured" : "Whole console",
          null,
          "all",
          family.later_yes,
          none && family.collection_enabled
            ? {
                state: NOT_STARTED,
                reason: "No source is configured; a source is read when you enable its profile.",
              }
            : undefined,
        ),
      );
    }
  }
  return rows;
}
