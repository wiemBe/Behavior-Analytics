// Search cadence: how often the platform queries QRadar, and what that costs.
//
// Pure helpers only. The seven presets are the whole vocabulary the operator
// is offered (docs/SEARCH-CADENCE.md §SC.6); the API accepts nothing else for
// a family, and a scheduled search shows anything else as "Custom".

import { ApiError, actionErrorMessage, type SearchFamily } from "@/lib/api";

/** One duration as `1 h 7 min`, `5 min`, `8 h` or `30 s`. Zero parts are dropped. */
export function formatDuration(totalSeconds: number): string {
  const total = Math.round(totalSeconds);
  if (!Number.isFinite(total) || total < 0) return "—";
  if (total === 0) return "0 s";
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const parts: string[] = [];
  if (h) parts.push(`${h} h`);
  if (m) parts.push(`${m} min`);
  if (s) parts.push(`${s} s`);
  return parts.join(" ");
}

/** A bucket width. Null means the family has no buckets, which is not zero. */
export function formatBucket(seconds: number | null): string {
  return seconds == null ? "—" : formatDuration(seconds);
}

export interface WorstCaseDelay {
  seconds: number;
  /** Without a bucket the figure bounds evidence arrival, not detection. */
  evidenceOnly: boolean;
  text: string;
}

/**
 * The longest an event can wait before the platform has looked at it:
 * one cadence, plus the bucket it falls in, plus the settle margin.
 *
 * A family with no bucket (anomaly explanations) has no detection to delay;
 * the figure there bounds when evidence arrives, and says so.
 */
export function worstCaseDelay(
  cadenceSeconds: number,
  bucketSeconds: number | null,
  settleSeconds: number,
): WorstCaseDelay {
  const evidenceOnly = bucketSeconds == null;
  const seconds = cadenceSeconds + (bucketSeconds ?? 0) + settleSeconds;
  const span = formatDuration(seconds);
  return { seconds, evidenceOnly, text: evidenceOnly ? `evidence up to ${span}` : `up to ${span}` };
}

export const FAMILY_INFO: Record<SearchFamily, { name: string; scope: string }> = {
  PHASE_A_VOLUME: {
    name: "Phase A volume",
    scope: "Event count per log source",
  },
  WAF_COLLECTION: {
    name: "WAF collection",
    scope: "Endpoint, status and client breakdowns",
  },
  ANOMALY_EXPLANATION: {
    name: "Anomaly explanations",
    scope: "Evidence for open anomalies",
  },
  RULE_MATCH: {
    name: "Rule matches",
    scope: "Events matched per rule",
  },
  CATCHALL_SENDERS: {
    name: "Catch-all senders",
    scope: "Senders feeding SIM Generic and the Universal DSMs",
  },
};

/** Name and scope for a family. An unknown family keeps its raw key. */
export function familyInfo(family: string): { name: string; scope: string | null } {
  return FAMILY_INFO[family as SearchFamily] ?? { name: family, scope: null };
}

export interface SchedulePreset {
  seconds: number;
  /** `5 min` … `8 h`. */
  label: string;
  cron: string;
}

/** The §SC.6 presets, shortest first. */
export const SCHEDULE_PRESETS: readonly SchedulePreset[] = [
  { seconds: 300, label: formatDuration(300), cron: "*/5 * * * *" },
  { seconds: 900, label: formatDuration(900), cron: "*/15 * * * *" },
  { seconds: 1800, label: formatDuration(1800), cron: "*/30 * * * *" },
  { seconds: 3600, label: formatDuration(3600), cron: "0 * * * *" },
  { seconds: 7200, label: formatDuration(7200), cron: "0 */2 * * *" },
  { seconds: 14400, label: formatDuration(14400), cron: "0 */4 * * *" },
  { seconds: 28800, label: formatDuration(28800), cron: "0 */8 * * *" },
];

/** The preset an exact cron string belongs to, or null for a custom schedule. */
export function cronToPreset(cron: string): SchedulePreset | null {
  return SCHEDULE_PRESETS.find((p) => p.cron === cron) ?? null;
}

/** The cron for a preset given in seconds, or null if it is not a preset. */
export function presetToCron(seconds: number): string | null {
  return SCHEDULE_PRESETS.find((p) => p.seconds === seconds)?.cron ?? null;
}

/** `Every 1 h` for a preset cron, `Custom` for anything else. */
export function scheduleLabel(cron: string): string {
  const preset = cronToPreset(cron);
  return preset ? `Every ${preset.label}` : "Custom";
}

/** A cadence option label from the API's allowed values. */
export function cadenceLabel(seconds: number): string {
  return formatDuration(seconds);
}

export type OutcomeTone = "ok" | "warn" | "crit" | "neutral";

/**
 * A family's last-run outcome as the backend records it (`ok`, `partial`,
 * `failed`, `no-instance`, `no-sources`, `dispatched`). A value this build does not know has no label: the
 * caller shows it verbatim rather than guessing what it means.
 */
export function outcomeInfo(
  outcome: string,
): { label: string; tone: OutcomeTone } | null {
  switch (outcome) {
    case "ok":
      return { label: "Complete", tone: "ok" };
    case "partial":
      return { label: "Partial", tone: "warn" };
    case "failed":
      return { label: "Failed", tone: "crit" };
    case "no-instance":
      return { label: "No enabled instance", tone: "neutral" };
    case "no-sources":
      return { label: "No eligible WAF source", tone: "neutral" };
    case "dispatched":
      return { label: "Dispatched", tone: "ok" };
    default:
      return null;
  }
}

/**
 * The line shown when a settings change is refused or fails.
 *
 * The API's answer is the source of truth, so permissions are never judged in
 * advance; this only words the refusal. A 403 names the permission; anything
 * else says what failed and what the analyst can do. Both end by saying the
 * setting is unchanged, because the control has already reverted.
 */
export function changeFailure(
  err: unknown,
  forbidden: string,
): { kind: "forbidden" | "error"; text: string } {
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", text: `${forbidden} Nothing was changed.` };
  }
  return { kind: "error", text: `Not changed. ${actionErrorMessage(err)}` };
}
