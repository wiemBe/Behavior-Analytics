// Presentation rules for rule-match anomalies (SOC-6e).
//
// Pure functions. A figure the API did not send is the dash and never a zero; a measured zero
// (a silence's observed count) stays "0".

import type { AnomalySummary } from "@/lib/api";
import { formatMetric } from "@/lib/behavior";
import { wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { formatCount, type FormatLocale } from "@/lib/format";

export const RULE_TYPES = ["RULE_SILENCE", "RULE_STORM"] as const;

export const MISSING = "—";

export function isRuleType(type: string | null | undefined): boolean {
  return type != null && (RULE_TYPES as readonly string[]).includes(type);
}

/** A rule-match detector's name; a type this build does not know shows as sent. */
export function ruleTypeLabel(type: string, i18n: BehaviorI18n): string {
  return isRuleType(type) ? wordOrValue(i18n, "enums.ruleAnomalyType", type) : type;
}

export function isRuleRow(a: Pick<AnomalySummary, "subject_kind" | "rule">): boolean {
  return a.subject_kind === "RULE" && a.rule != null;
}

/** Matched events per window: whole numbers as counts, a fractional expectation to one decimal. */
export function formatMatches(value: number | null | undefined, locale: FormatLocale = "en"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  return Number.isInteger(value) ? formatCount(value, locale) : formatMetric(value, 1, locale);
}

/** A ratio to the expected count, with the multiplication sign as its unit. */
export function formatTimes(value: number | null | undefined, locale: FormatLocale = "en"): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  return `${formatMetric(value, 2, locale)} ×`;
}

/** The width of one collection window: 3600 is "1 h", 300 is "5 min", anything odd is seconds. */
export function windowWidthText(seconds: number): string {
  if (seconds > 0 && seconds % 86400 === 0) return `${seconds / 86400} d`;
  if (seconds > 0 && seconds % 3600 === 0) return `${seconds / 3600} h`;
  if (seconds > 0 && seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}
