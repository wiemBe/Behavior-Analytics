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
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { fixed, plainCount } from "@/lib/figureText";

import { ENGLISH_HISTORY, historyWord, plainSentence, type SentenceReader } from "./historyText";

export const READ_PERMISSION = "read:history_backfill";
export const ADMIN_PERMISSION = "admin:history_backfill";

/** The family as the analyst reads it. A family this build does not know is shown as recorded. */
export function familyName(family: string, i18n: BehaviorI18n = ENGLISH_HISTORY): string {
  return historyWord(i18n, "family", family);
}

export type Tone = "neutral" | "ok" | "warn" | "crit" | "info";

export interface StateInfo {
  label: string;
  tone: Tone;
}

const TONES: Record<string, Tone> = {
  NOT_REQUESTED: "neutral",
  WAITING: "neutral",
  READING: "info",
  COMPLETE: "ok",
  PARTIAL: "warn",
  UNAVAILABLE: "neutral",
  NOT_ELIGIBLE: "neutral",
};

/** The stored state (or one of the rows' own: SWITCHED_OFF, NOT_STARTED, NOT_READ) as a pill reads it. */
export function stateInfo(state: string, i18n: BehaviorI18n = ENGLISH_HISTORY): StateInfo {
  const tone = Object.prototype.hasOwnProperty.call(TONES, state) ? TONES[state] : "neutral";
  return { label: historyWord(i18n, "state", state), tone };
}

/** The two states of a row that has no scope row yet; they are not stored states. */
export const SWITCHED_OFF: StateInfo = stateInfo("SWITCHED_OFF");
export const NOT_STARTED: StateInfo = stateInfo("NOT_STARTED");
export const NOT_READ: StateInfo = stateInfo("NOT_READ");

/** What each console mode means, in the words the decision block opens with (English). */
export const MODE_TEXT: Record<HistoryConsoleMode, string> = {
  UNDECIDED: "Not decided",
  NO: "No: history is not read",
  ACTIVE: "Yes: history is read",
  STOPPED: "Yes: reading was stopped",
};

export function modeText(mode: string, i18n: BehaviorI18n = ENGLISH_HISTORY): string {
  return historyWord(i18n, "mode", mode);
}

/** "the installer", or the person who answered in the UI. */
export function decidedBy(
  decision: { source: string; decided_by: string },
  i18n: BehaviorI18n = ENGLISH_HISTORY,
): string {
  return decision.source === "INSTALLER"
    ? i18n.t("history.decidedBy.installer")
    : decision.decided_by;
}

/** A figure of days read: whole days without a decimal, anything else with one, 0.0 as 0.0. */
export function formatDays(value: number, locale: "en" | "tr" = "en"): string {
  return Number.isInteger(value) && value > 0 ? plainCount(value, locale) : fixed(value, 1, locale);
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

export function questionText(days: number | null, i18n: BehaviorI18n = ENGLISH_HISTORY): string {
  return days === null
    ? i18n.t("history.dialog.question")
    : i18n.t("history.dialog.questionDays", { days: plainCount(days, i18n.locale) });
}

/** Why the family is switched off, in the interface language. */
export function collectionReason(family: HistoryFamily, read: SentenceReader = plainSentence): string {
  return read(family.collection_reason, {
    code: family.collection_reason_code,
    params: family.collection_reason_params,
  });
}

/** Why the family has no window, or how its window was reduced, in the interface language. */
export function windowReason(
  family: HistoryFamily,
  i18n: BehaviorI18n = ENGLISH_HISTORY,
  read: SentenceReader = plainSentence,
): string {
  const { reason, reason_code, reason_params } = family.window;
  return reason === null
    ? i18n.t("history.familyState.windowNotFitted")
    : read(reason, { code: reason_code, params: reason_params });
}

/** What the family is, before any estimate: its state in the dialog's "families" column. */
export function familyStateText(
  family: HistoryFamily,
  i18n: BehaviorI18n = ENGLISH_HISTORY,
  read: SentenceReader = plainSentence,
): string {
  if (!family.collection_enabled) {
    return i18n.t("history.familyState.switchedOff", { reason: collectionReason(family, read) });
  }
  if (!family.window.available) {
    return i18n.t("history.familyState.unavailable", { reason: windowReason(family, i18n, read) });
  }
  if (family.sources !== null) {
    const waiting = family.sources.length;
    const collected = family.sources.filter((s) => s.later_yes !== "possible").length;
    const rest =
      collected > 0
        ? i18n.t("history.familyState.collected", {
            collected: plainCount(collected, i18n.locale),
          })
        : i18n.t("history.familyState.later");
    return i18n.t("history.familyState.sources", { count: waiting, rest });
  }
  return i18n.t("history.familyState.willRead");
}

/** The estimate of one family in words. A missing figure shows its reason, never a zero. */
export function estimateText(
  estimate: HistoryFamilyEstimate | undefined,
  i18n: BehaviorI18n = ENGLISH_HISTORY,
  read: SentenceReader = plainSentence,
): {
  figures: string;
  note: string | null;
} {
  if (estimate === undefined) {
    return { figures: "—", note: i18n.t("history.estimate.none") };
  }
  const reason = read(estimate.reason, {
    code: estimate.reason_code,
    params: estimate.reason_params,
  });
  if (estimate.refused) {
    return { figures: i18n.t("history.estimate.refused"), note: reason };
  }
  if (estimate.searches === null) {
    return { figures: "—", note: reason };
  }
  const time =
    estimate.hours === null
      ? i18n.t("history.estimate.notEstimated")
      : i18n.t("history.estimate.about", { hours: plainCount(estimate.hours, i18n.locale) });
  const figures = i18n.t("history.estimate.figures", {
    searches: estimate.searches,
    time,
  });
  if (estimate.reduced) {
    return {
      figures,
      note: i18n.t("history.estimate.reduced", { reason }),
    };
  }
  return { figures, note: null };
}

/** The assumptions of an estimate in the interface language, each once, in the API's order. */
export function assumptionTexts(
  estimate: HistoryFamilyEstimate | undefined,
  read: SentenceReader = plainSentence,
): string[] {
  const codes = estimate?.assumption_codes ?? [];
  const texts = (estimate?.assumptions ?? []).map((text, index) => read(text, codes[index]));
  return [...new Set(texts)];
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

/** The sentence a 409 carries, in the interface language (its code sits beside it in the body). */
export function conflictDetail(
  err: ApiError,
  detail: string,
  read: SentenceReader = plainSentence,
): string {
  const body = err.body as
    | { detail_code?: string | null; detail_params?: Record<string, unknown> | null }
    | null
    | undefined;
  return read(detail, { code: body?.detail_code ?? null, params: body?.detail_params ?? null });
}

/** The console a 409 carries, when its body has one. */
export function conflictConsole(err: unknown): HistoryConsole | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const body = err.body as { console?: HistoryConsole | null } | null | undefined;
  const console = body?.console;
  return console && typeof console === "object" ? console : null;
}

// --- what each action does (HISTORY-BACKFILL §B.5) ---------------------------

/**
 * The English of what each action does. The dialog and the decision block read the same sentences
 * from the `history.confirm` messages (`confirmText`); a test pins that the English is equal.
 */
export const CONFIRM_TEXT = {
  yes: (days: number | null) =>
    `This records Yes${days === null ? "" : ` for ${days} days`}. At its next collection each family reads the days before its first live data: source volume, catch-all senders and rule matches always, a WAF or IPS source only if it has not been collected yet. Nothing old raises an alert. A Yes is never reversed; reading can be stopped.`,
  no: "This records No. No history is read. You can still choose Read history now later.",
  start:
    "This asks every family to read history again at its next collection: source volume, catch-all senders and rule matches, including sources added since, and a WAF or IPS source only if it has not been collected yet. Nothing is read at once and nothing old raises an alert.",
  stop: "This ends every scope that is reading as Partial, with the reason \"stopped by\" and your name. What was read stays and is used for learning. Nothing is deleted. Read history now starts it again.",
} as const;

/** What the action does, in the interface language (the `history.confirm` messages). */
export function confirmText(
  action: "yes" | "no" | "start" | "stop",
  days: number | null,
  i18n: BehaviorI18n = ENGLISH_HISTORY,
): string {
  if (action === "yes") {
    const clause =
      days === null
        ? ""
        : i18n.t("history.confirm.yesDays", { days: plainCount(days, i18n.locale) });
    return i18n.t("history.confirm.yes", { days: clause });
  }
  return i18n.t(`history.confirm.${action}`);
}
