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

import type { BehaviorI18n } from "@/lib/behaviorI18n";

import { stateInfo, type StateInfo } from "./historyFormat";
import { ENGLISH_HISTORY } from "./historyText";

export interface HistoryRow {
  key: string;
  family: string;
  scope: string;
  scopeSub: string | null;
  state: StateInfo;
  /** The state as the API (or the row itself) names it: what code compares, never the label. */
  stateCode: string;
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

function scopeRow(
  console: HistoryConsole,
  family: HistoryFamily,
  scope: HistoryScope,
  i18n: BehaviorI18n,
): HistoryRow {
  const perSource = family.sources !== null;
  const last =
    scope.through !== null || scope.finished_at !== null
      ? { through: scope.through, finished: scope.finished_at }
      : null;
  const target = scope.days_target ?? scope.days_requested;
  return {
    key: `${console.instance_id}:${family.family}:${scope.scope_key}`,
    family: family.family,
    scope: perSource
      ? (scope.name ?? i18n.t("history.scope.logSource", { key: scope.scope_key }))
      : i18n.t("history.scope.whole"),
    scopeSub:
      perSource && scope.name ? i18n.t("history.scope.logSource", { key: scope.scope_key }) : null,
    state: stateInfo(scope.state, i18n),
    stateCode: scope.state,
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
interface Absent {
  state: StateInfo;
  stateCode: string;
  reason: string;
}

function absentState(console: HistoryConsole, family: HistoryFamily, i18n: BehaviorI18n): Absent {
  const t = i18n.t;
  const absent = (code: string, reason: string): Absent => ({
    state: stateInfo(code, i18n),
    stateCode: code,
    reason,
  });
  if (!family.collection_enabled) return absent("SWITCHED_OFF", family.collection_reason);
  if (!family.window.available) {
    return absent("UNAVAILABLE", family.window.reason ?? t("history.familyState.windowNotFitted"));
  }
  if (console.mode === "UNDECIDED") return absent("NOT_READ", t("history.row.noDecision"));
  if (console.mode === "NO") return absent("NOT_READ", t("history.row.answerNo"));
  if (console.mode === "STOPPED") return absent("NOT_READ", t("history.row.stopped"));
  return absent("NOT_STARTED", t("history.row.created"));
}

function absentRow(
  console: HistoryConsole,
  family: HistoryFamily,
  scope: string,
  scopeSub: string | null,
  keyTail: string,
  laterYes: string | null,
  i18n: BehaviorI18n,
  override?: Absent,
): HistoryRow {
  const { state, stateCode, reason } = override ?? absentState(console, family, i18n);
  const window = family.window;
  return {
    key: `${console.instance_id}:${family.family}:${keyTail}`,
    family: family.family,
    scope,
    scopeSub,
    state,
    stateCode,
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

export function rowsOf(console: HistoryConsole, i18n: BehaviorI18n = ENGLISH_HISTORY): HistoryRow[] {
  const rows: HistoryRow[] = [];
  for (const family of console.families) {
    const before = rows.length;
    for (const scope of family.scopes) rows.push(scopeRow(console, family, scope, i18n));
    for (const source of family.sources ?? []) {
      let override: Absent | undefined;
      if (!source.profile_enabled) {
        override = {
          state: stateInfo("PROFILE_DISABLED", i18n),
          stateCode: "PROFILE_DISABLED",
          reason: i18n.t("history.row.profileDisabled"),
        };
      }
      rows.push(
        absentRow(
          console,
          family,
          source.name,
          i18n.t("history.scope.logSource", { key: source.scope_key }),
          `source:${source.scope_key}`,
          source.later_yes,
          i18n,
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
          none ? i18n.t("history.scope.noSource") : i18n.t("history.scope.whole"),
          null,
          "all",
          family.later_yes,
          i18n,
          none && family.collection_enabled
            ? {
                state: stateInfo("NOT_STARTED", i18n),
                stateCode: "NOT_STARTED",
                reason: i18n.t("history.row.noSourceReason"),
              }
            : undefined,
        ),
      );
    }
  }
  return rows;
}
