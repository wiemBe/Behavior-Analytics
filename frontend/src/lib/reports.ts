// The reports page's rules (SOC-8c): which period the URL names, how the calendar periods step,
// and where a table cell drills to. Pure, so the page and its tests share one definition.
//
// Nothing in the URL is trusted. An unknown period falls back to the default and says so; a
// calendar `start` that is not a boundary, or lies after the current period, is replaced by the
// current period and says so. The API would refuse both (`reports.start_not_boundary`,
// `reports.start_in_future`), and the analyst should land on a report, not an error.

import type { ActiveFamily, ReportPeriod, SubjectKind } from "./api";
import { instant, urlInstant } from "./timeRange";

export const REPORT_PERIODS: readonly ReportPeriod[] = [
  "last_7d",
  "last_30d",
  "calendar_week",
  "calendar_month",
];
export const DEFAULT_PERIOD: ReportPeriod = "last_7d";

/** Mirrors the backend's `MIN_FIGURE_N` (`app/services/reports.py`): below it a median is null. */
export const MIN_FIGURE_N = 5;

/** The most rows one CSV export holds (`api_max_export_rows` default, SOC-8a). */
export const EXPORT_MAX_ROWS = 10_000;

export type ReportIssue = "unknownPeriod" | "badStart" | "futureStart";

export interface ReportQuery {
  period: ReportPeriod;
  /** The calendar period's first instant, ISO with `Z`; null for the sliding periods. */
  start: string | null;
  issues: ReportIssue[];
}

type Raw = string | string[] | undefined;

export const isCalendar = (period: ReportPeriod): boolean =>
  period === "calendar_week" || period === "calendar_month";

const DAY_MS = 86_400_000;

/** The first instant of the calendar period that holds `ms`: Monday 00:00 UTC or the 1st 00:00 UTC. */
export function calendarStart(period: ReportPeriod, ms: number): string {
  const d = new Date(ms);
  if (period === "calendar_month") {
    return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1)).toISOString();
  }
  const midnight = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const sinceMonday = (d.getUTCDay() + 6) % 7;
  return new Date(midnight - sinceMonday * DAY_MS).toISOString();
}

/** The calendar period after (`1`) or before (`-1`) the one starting at `start`. */
export function stepStart(period: ReportPeriod, start: string, dir: 1 | -1): string {
  const d = new Date(start);
  if (period === "calendar_month") {
    return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + dir, 1)).toISOString();
  }
  return new Date(d.getTime() + dir * 7 * DAY_MS).toISOString();
}

const first = (value: Raw): string | undefined => (Array.isArray(value) ? value[0] : value);

export function parseReportQuery(
  params: Record<string, Raw>,
  nowMs: number,
): ReportQuery {
  const issues: ReportIssue[] = [];
  const rawPeriod = first(params.period)?.trim() ?? "";
  const known = REPORT_PERIODS.find((p) => p === rawPeriod);
  if (rawPeriod && !known) issues.push("unknownPeriod");
  const period = known ?? DEFAULT_PERIOD;
  if (!isCalendar(period)) return { period, start: null, issues };

  const current = calendarStart(period, nowMs);
  const rawStart = first(params.start)?.trim() ?? "";
  if (!rawStart) return { period, start: current, issues };
  const parsed = instant(rawStart);
  // A boundary is the instant that is its own calendar start.
  if (!parsed || calendarStart(period, Date.parse(parsed)) !== parsed) {
    issues.push("badStart");
    return { period, start: current, issues };
  }
  if (Date.parse(parsed) > Date.parse(current)) {
    issues.push("futureStart");
    return { period, start: current, issues };
  }
  return { period, start: parsed, issues };
}

/** The page's path for a period; the default period and the current calendar period stay bare. */
export function reportHref(period: ReportPeriod, start: string | null, nowMs: number): string {
  const pairs: Array<[string, string]> = [];
  if (period !== DEFAULT_PERIOD) pairs.push(["period", period]);
  if (isCalendar(period) && start && start !== calendarStart(period, nowMs)) {
    pairs.push(["start", urlInstant(start)]);
  }
  const qs = new URLSearchParams(pairs).toString();
  return qs ? `/reports?${qs}` : "/reports";
}

/** True when `start` opens the period that holds `nowMs`: there is no later period to step to. */
export function isCurrentPeriod(period: ReportPeriod, start: string, nowMs: number): boolean {
  return start === calendarStart(period, nowMs);
}

/** The anomaly list filters by subject kind; `IPS` is its URL-only value for both IPS kinds. */
const SUBJECT_OF_FAMILY: Record<ActiveFamily, SubjectKind | "IPS"> = {
  log_source: "LOG_SOURCE",
  waf: "WAF_ENDPOINT",
  ips: "IPS",
  property_watch: "PW_VALUE",
  rule: "RULE",
};

export function subjectKindOf(family: ActiveFamily): SubjectKind | "IPS" {
  return SUBJECT_OF_FAMILY[family];
}

function rangePairs(range: { start: string; end: string }): Array<[string, string]> {
  return [
    ["since", urlInstant(range.start)],
    ["until", urlInstant(range.end)],
  ];
}

/** The alert list under the cell's family, severity and the report's range. */
export function alertsDrillHref(
  cell: { family: string; severity: string },
  range: { start: string; end: string },
): string {
  const qs = new URLSearchParams([
    ["family", cell.family],
    ["severity", cell.severity],
    ...rangePairs(range),
  ]);
  return `/alerts?${qs.toString()}`;
}

/** The anomaly list under the cell's family, severity and the report's range. */
export function anomaliesDrillHref(
  cell: { family: ActiveFamily; severity: string },
  range: { start: string; end: string },
): string {
  const qs = new URLSearchParams([
    ["subject_kind", subjectKindOf(cell.family)],
    ["severity", cell.severity],
    ...rangePairs(range),
  ]);
  return `/anomalies?${qs.toString()}`;
}

/** The export parameters of a whole period: the list's own `since` and `until`. */
export function periodExportParams(range: { start: string; end: string }): {
  since: string;
  until: string;
} {
  return { since: urlInstant(range.start), until: urlInstant(range.end) };
}

/** A block's range ends at the moment the report was read (a sliding or current period). */
export function endsAtNow(range: { end: string; generated_at: string }): boolean {
  return Date.parse(range.end) >= Date.parse(range.generated_at);
}
