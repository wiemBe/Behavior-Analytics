// The words of the history backfill: family names, states, the console modes and the sentences
// that say what an action does. Nothing here invents a figure: a missing measurement is an em
// dash with its reason, never a zero (guidelines §4).

import {
  ApiError,
  type HistoryConsole,
  type HistoryConsoleEstimate,
  type HistoryConsoleMode,
  type HistoryFamily,
  type HistoryFamilyEstimate,
} from "@/lib/api";
import { formatCount } from "@/lib/format";


export const READ_PERMISSION = "read:history_backfill";
export const ADMIN_PERMISSION = "admin:history_backfill";

const FAMILY_NAMES: Record<string, string> = {
  PHASE_A: "Source volume",
  WAF: "WAF",
  IPS: "IPS",
  CATCHALL: "Catch-all senders",
  RULE_MATCH: "Rule matches",
};

/** The family as the analyst reads it. A family this build does not know is shown as recorded. */
export function familyName(family: string): string {
  return FAMILY_NAMES[family] ?? family;
}

export type Tone = "neutral" | "ok" | "warn" | "crit" | "info";

export interface StateInfo {
  label: string;
  tone: Tone;
}

const STATES: Record<string, StateInfo> = {
  NOT_REQUESTED: { label: "Not requested", tone: "neutral" },
  WAITING: { label: "Waiting", tone: "neutral" },
  READING: { label: "Reading", tone: "info" },
  COMPLETE: { label: "Complete", tone: "ok" },
  PARTIAL: { label: "Partial", tone: "warn" },
  UNAVAILABLE: { label: "Unavailable", tone: "neutral" },
  NOT_ELIGIBLE: { label: "Not eligible", tone: "neutral" },
};

/** The two states of a row that has no scope row yet; they are not stored states. */
export const SWITCHED_OFF: StateInfo = { label: "Switched off", tone: "neutral" };
export const NOT_STARTED: StateInfo = { label: "Not started", tone: "neutral" };
export const NOT_READ: StateInfo = { label: "Not read", tone: "neutral" };

export function stateInfo(state: string): StateInfo {
  return STATES[state] ?? { label: state, tone: "neutral" };
}

/** What each console mode means, in the words the decision block opens with. */
export const MODE_TEXT: Record<HistoryConsoleMode, string> = {
  UNDECIDED: "Not decided",
  NO: "No: history is not read",
  ACTIVE: "Yes: history is read",
  STOPPED: "Yes: reading was stopped",
};

export function modeText(mode: string): string {
  return (MODE_TEXT as Record<string, string>)[mode] ?? mode;
}

/** "the installer", or the person who answered in the UI. */
export function decidedBy(decision: { source: string; decided_by: string }): string {
  return decision.source === "INSTALLER" ? "the installer" : decision.decided_by;
}

/** A figure of days read: whole days without a decimal, anything else with one, 0.0 as 0.0. */
export function formatDays(value: number): string {
  return Number.isInteger(value) && value > 0 ? String(value) : value.toFixed(1);
}

export { formatCount } from "@/lib/format";

export { relativeAge } from "@/lib/time";

// --- the dialog -------------------------------------------------------------

/** The days the decision setting asks for, from the estimate first and the status second. */
export function requestedDays(
  consoles: readonly HistoryConsole[],
  estimates: readonly HistoryConsoleEstimate[] | null,
): number | null {
  for (const e of estimates ?? []) {
    for (const f of e.families) return f.days_requested;
  }
  for (const c of consoles) {
    for (const f of c.families) return f.window.days_requested;
  }
  return null;
}

export function questionText(days: number | null): string {
  return days === null ? "Read QRadar's past?" : `Read the last ${days} days from QRadar?`;
}

/** What the family is, before any estimate: its state in the dialog's "families" column. */
export function familyStateText(family: HistoryFamily): string {
  if (!family.collection_enabled) {
    return `Switched off. ${family.collection_reason}.`;
  }
  if (!family.window.available) {
    return `Unavailable. ${family.window.reason ?? "The window could not be fitted."}`;
  }
  if (family.sources !== null) {
    const waiting = family.sources.length;
    const collected = family.sources.filter((s) => s.later_yes !== "possible").length;
    const head = `${waiting} ${waiting === 1 ? "source" : "sources"} enabled`;
    const rest =
      collected > 0
        ? `${collected} already collected and cannot read history; sources you enable later do`
        : "sources you enable later are read when you enable them";
    return `${head}; ${rest}.`;
  }
  return "Will be read.";
}

/** The estimate of one family in words. A missing figure shows its reason, never a zero. */
export function estimateText(estimate: HistoryFamilyEstimate | undefined): {
  figures: string;
  note: string | null;
} {
  if (estimate === undefined) {
    return { figures: "—", note: "No estimate was returned for this family." };
  }
  if (estimate.refused) {
    return { figures: "Refused", note: estimate.reason };
  }
  if (estimate.searches === null) {
    return { figures: "—", note: estimate.reason };
  }
  const hours =
    estimate.hours === null ? "time not estimated" : `about ${estimate.hours} hours`;
  const figures = `${formatCount(estimate.searches)} searches, ${hours}`;
  if (estimate.reduced) {
    return {
      figures,
      note: `Reduced: ${estimate.reason}`,
    };
  }
  return { figures, note: null };
}

/** The longest family estimate of a console: the families read side by side, each in its task. */
export function costHours(estimate: HistoryConsoleEstimate | undefined): number | null {
  if (estimate === undefined) return null;
  let longest: number | null = null;
  for (const f of estimate.families) {
    if (f.hours !== null && !f.refused && (longest === null || f.hours > longest)) {
      longest = f.hours;
    }
  }
  return longest;
}

// --- failures ---------------------------------------------------------------

/** The console a 409 carries, when its body has one. */
export function conflictConsole(err: unknown): HistoryConsole | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { console?: HistoryConsole | null } | null | undefined;
  const console = body?.console;
  return console && typeof console === "object" ? console : null;
}

// --- what each action does (HISTORY-BACKFILL §B.5) ---------------------------

export const CONFIRM_TEXT = {
  yes: (days: number | null) =>
    `This records Yes${days === null ? "" : ` for ${days} days`}. At its next collection each family reads the days before its first live data: source volume, catch-all senders and rule matches always, a WAF or IPS source only if it has not been collected yet. Nothing old raises an alert. A Yes is never reversed; reading can be stopped.`,
  no: "This records No. No history is read. You can still choose Read history now later.",
  start:
    "This asks every family to read history again at its next collection: source volume, catch-all senders and rule matches, including sources added since, and a WAF or IPS source only if it has not been collected yet. Nothing is read at once and nothing old raises an alert.",
  stop: "This ends every scope that is reading as Partial, with the reason \"stopped by\" and your name. What was read stays and is used for learning. Nothing is deleted. Read history now starts it again.",
} as const;
