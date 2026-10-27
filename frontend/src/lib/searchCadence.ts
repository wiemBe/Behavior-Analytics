// Search cadence: how often the platform queries QRadar, and what that costs.
//
// Pure helpers only. The seven presets are the whole vocabulary the operator
// is offered (docs/SEARCH-CADENCE.md §SC.6); the API accepts nothing else for
// a family, and a scheduled search shows anything else as "Custom".

import { ApiError, actionErrorMessage, type SearchFamily } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { ENGLISH_CADENCE, cadenceDuration } from "@/lib/cadenceText";
import { formatDuration } from "@/lib/time";

export { formatDuration };

/** A bucket width. Null means the family has no buckets, which is not zero. */
export function formatBucket(seconds: number | null, i18n?: BehaviorI18n): string {
  return seconds == null ? "—" : i18n ? cadenceDuration(seconds, i18n) : formatDuration(seconds);
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
  i18n: BehaviorI18n = ENGLISH_CADENCE,
): WorstCaseDelay {
  const evidenceOnly = bucketSeconds == null;
  const seconds = cadenceSeconds + (bucketSeconds ?? 0) + settleSeconds;
  const duration = cadenceDuration(seconds, i18n);
  return {
    seconds,
    evidenceOnly,
    text: i18n.t(evidenceOnly ? "cadence.delay.evidenceUpTo" : "cadence.delay.upTo", { duration }),
  };
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
  IPS_COLLECTION: {
    name: "IPS collection",
    scope: "Port, IP, signature and fan-out per source",
  },
  CATCHALL_SENDERS: {
    name: "Catch-all senders",
    scope: "Senders feeding SIM Generic and the Universal DSMs",
  },
  GEO_COLLECTION: {
    name: "Geography",
    scope: "Events per country, by source and destination address",
  },
  PROPERTY_WATCH: {
    name: "Property watches",
    scope: "Event count per watched property value",
  },
};

/**
 * Name and scope for a family. An unknown family keeps its raw key. With a translator the words
 * come from the `cadence.family` messages (the English of those equals `FAMILY_INFO`).
 */
export function familyInfo(
  family: string,
  i18n?: BehaviorI18n,
): { name: string; scope: string | null } {
  // `has` follows the prototype chain of the catalog objects, so a raw key such as "constructor"
  // is excluded first.
  if (i18n && !(family in Object.prototype) && i18n.t.has?.(`cadence.family.${family}.name`)) {
    return {
      name: i18n.t(`cadence.family.${family}.name`),
      scope: i18n.t(`cadence.family.${family}.scope`),
    };
  }
  return Object.prototype.hasOwnProperty.call(FAMILY_INFO, family)
    ? FAMILY_INFO[family as SearchFamily]
    : { name: family, scope: null };
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

/** A cadence option label from the API's allowed values. */
export function cadenceLabel(seconds: number, i18n?: BehaviorI18n): string {
  return i18n ? cadenceDuration(seconds, i18n) : formatDuration(seconds);
}

/** `Every 5 min` / `Her 5 dk`: a preset (or an allowed cadence) as an option or schedule reads. */
export function everyLabel(seconds: number, i18n: BehaviorI18n = ENGLISH_CADENCE): string {
  return i18n.t("cadence.schedule.every", { duration: cadenceDuration(seconds, i18n) });
}

export type OutcomeTone = "ok" | "warn" | "crit" | "neutral";

/**
 * A family's last-run outcome as the backend records it (`ok`, `partial`,
 * `failed`, `no-instance`, `no-sources`, `dispatched`). A value this build does not know has no label: the
 * caller shows it verbatim rather than guessing what it means.
 */
export function outcomeInfo(
  outcome: string,
  i18n: BehaviorI18n = ENGLISH_CADENCE,
): { label: string; tone: OutcomeTone } | null {
  if (!Object.prototype.hasOwnProperty.call(OUTCOME_TONE, outcome)) return null;
  return { label: i18n.t(`cadence.outcome.${outcome}`), tone: OUTCOME_TONE[outcome] };
}

const OUTCOME_TONE: Record<string, OutcomeTone> = {
  ok: "ok",
  partial: "warn",
  failed: "crit",
  "no-instance": "neutral",
  "no-sources": "neutral",
  dispatched: "ok",
};

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
  i18n: BehaviorI18n = ENGLISH_CADENCE,
): { kind: "forbidden" | "error"; text: string } {
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", text: i18n.t("cadence.change.nothingChanged", { forbidden }) };
  }
  return {
    kind: "error",
    text: i18n.t("cadence.change.notChanged", { reason: actionErrorMessage(err, i18n.t) }),
  };
}
