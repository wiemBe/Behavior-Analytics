// The pure parts of the QRadar access page: what a console's entries add up to, the order the
// table shows them in, the tone of a status and the polling bounds. Nothing here reads a catalog
// or the network.

import type { AccessConsole, AccessEntry, AccessStatus } from "@/lib/api";

/** Seconds between two reads while a check runs. */
export const POLL_SECONDS = 5;
/** The page stops asking after this long and says the check is still running in the worker. */
export const POLL_LIMIT_SECONDS = 180;

/** A status the token was refused: the API exists but this token may not read it, or the token is bad. */
const REFUSED: readonly AccessStatus[] = ["unauthorized", "forbidden", "not_found"];

export type StatusTone = "" | "warn" | "crit";

export function isRefused(entry: AccessEntry): boolean {
  return entry.status !== null && REFUSED.includes(entry.status);
}

/** Refused first, then every entry that was not answered or not checked, then the ones that answered. */
export function rank(entry: AccessEntry): 0 | 1 | 2 {
  if (isRefused(entry)) return 0;
  return entry.status === "ok" ? 2 : 1;
}

/** Stable: the catalog's own order holds within a group. */
export function orderEntries(entries: readonly AccessEntry[]): AccessEntry[] {
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => rank(a.entry) - rank(b.entry) || a.index - b.index)
    .map((item) => item.entry);
}

/** ok is neutral. A 401 is an error. Every other status that is not an answer of "yes" warns. */
export function toneOf(status: AccessStatus | null): StatusTone {
  if (status === null || status === "ok") return "";
  return status === "unauthorized" ? "crit" : "warn";
}

export type VerdictKind =
  | "notApplicable"
  | "unauthorized"
  | "refused"
  | "notAnswered"
  | "partlyUnchecked"
  | "notChecked"
  | "allOk";

export interface Verdict {
  kind: VerdictKind;
  /** The titles the verdict names: the refused APIs, the unanswered ones or the unchecked ones. */
  titles: string[];
  count: number;
}

/**
 * One console's verdict. A 401 on any entry outranks the rest: the token itself is not accepted, so
 * the other statuses say nothing about what it may read. An entry that was never checked is never
 * counted as reachable, and a console with nothing checked is "not checked yet", not "all ok".
 */
export function verdictOf(console: AccessConsole): Verdict {
  if (!console.applicable) return { kind: "notApplicable", titles: [], count: 0 };
  const entries = console.entries;
  const named = (pick: (entry: AccessEntry) => boolean): string[] =>
    entries.filter(pick).map((entry) => entry.title);
  const unauthorized = named((entry) => entry.status === "unauthorized");
  if (unauthorized.length > 0) {
    return { kind: "unauthorized", titles: unauthorized, count: unauthorized.length };
  }
  if (entries.length === 0 || entries.every((entry) => entry.status === null)) {
    return { kind: "notChecked", titles: [], count: 0 };
  }
  const refused = named(isRefused);
  if (refused.length > 0) return { kind: "refused", titles: refused, count: refused.length };
  const silent = named((entry) => entry.status === "unreachable" || entry.status === "error");
  if (silent.length > 0) return { kind: "notAnswered", titles: silent, count: silent.length };
  const unchecked = named((entry) => entry.status === null);
  if (unchecked.length > 0) {
    return { kind: "partlyUnchecked", titles: unchecked, count: unchecked.length };
  }
  return { kind: "allOk", titles: [], count: 0 };
}

export function verdictTone(kind: VerdictKind): "" | "warn" | "crit" {
  if (kind === "unauthorized") return "crit";
  if (kind === "refused" || kind === "notAnswered" || kind === "partlyUnchecked") return "warn";
  return "";
}

/** Whether any console of the answer has a check running. */
export function anyRunning(consoles: readonly AccessConsole[]): boolean {
  return consoles.some((console) => console.applicable && console.check_running);
}

/** The reason to show beside a status the page words itself: `previous_status` only when it changed. */
export function changedFrom(entry: AccessEntry): AccessStatus | null {
  return entry.previous_status !== null && entry.previous_status !== entry.status
    ? entry.previous_status
    : null;
}

/** Sync names the API reports, in the words the page uses. An unknown name is shown as sent. */
const SYNC_WORDS: Readonly<Record<string, string>> = {
  log_source_inventory: "log source inventory",
  rule_inventory: "rule inventory",
  offense_snapshot: "offense snapshot",
  log_source_metrics: "log source volume collection",
  health_cache: "QRadar health reads",
};

export function syncWord(name: string): string {
  return SYNC_WORDS[name] ?? name.replace(/_/g, " ");
}

export type AskKind = "verified" | "unauthorized" | "notFound" | "hint" | "none";

/**
 * What a refused entry asks the administrator for. A 404 is only that: a capability does not make
 * an API appear, so no hint or capability is ever offered with it.
 */
export function askKind(entry: AccessEntry): AskKind {
  if (entry.status === "unauthorized") return "unauthorized";
  if (entry.status === "not_found") return "notFound";
  if (entry.capability_verified && entry.required_capability !== null) return "verified";
  return entry.capability_hint ? "hint" : "none";
}

/** The ask a console says once, when every row of it would carry the same sentence; else null. */
export function sharedAsk(entries: readonly AccessEntry[]): AccessEntry | null {
  const refused = entries.filter(isRefused);
  if (refused.length === 0) return null;
  if (refused.some((entry) => entry.status === "unauthorized")) {
    return refused.find((entry) => entry.status === "unauthorized") ?? null;
  }
  if (refused.length < 2) return null;
  const key = (entry: AccessEntry): string => {
    const kind = askKind(entry);
    return `${kind}|${kind === "hint" ? entry.capability_hint : kind === "verified" ? entry.required_capability : ""}`;
  };
  const first = key(refused[0]);
  return refused.every((entry) => key(entry) === first) ? refused[0] : null;
}
