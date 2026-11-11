// Shared summaries for the reports tests.

import type {
  ReportAlertsBlock,
  ReportAnomaliesBlock,
  ReportAnomalyClocksBlock,
  ReportClock,
  ReportFamilyCoverage,
  ReportNoiseBlock,
  ReportSummary,
} from "@/lib/api";

export const START = "2026-11-04T09:00:00Z";
export const END = "2026-11-11T09:00:00Z";

export const clock = (median: number | null, n: number, over: Partial<ReportClock> = {}): ReportClock => ({
  median_seconds: median,
  n,
  state: median === null ? "INSUFFICIENT_DATA" : "OK",
  ...over,
});

export const FULL_COVERAGE: ReportFamilyCoverage[] = (
  ["log_source", "waf", "ips", "property_watch", "rule"] as const
).map((family) => ({ family, state: "OK", retention_days: 180, covered_from: null }));

export function summary(over: Partial<ReportSummary> = {}): ReportSummary {
  const alerts: ReportAlertsBlock = {
    state: "OK",
    permission: null,
    reason_code: null,
    rows: [
      { family: "log_source", severity: "HIGH", count: 2 },
      { family: "log_source", severity: "MEDIUM", count: 12 },
      { family: "waf", severity: "CRITICAL", count: 1 },
      { family: null, severity: "LOW", count: 3 },
    ],
    mtta: clock(8040, 18),
    mttr: clock(null, 3),
  };
  const anomalies: ReportAnomaliesBlock = {
    state: "OK",
    permission: null,
    reason_code: null,
    rows: [
      { family: "log_source", severity: "HIGH", count: 5 },
      { family: "waf", severity: "MEDIUM", count: 9 },
      { family: "ips", severity: "HIGH", count: 4 },
    ],
    coverage: FULL_COVERAGE,
  };
  const anomaly_clocks: ReportAnomalyClocksBlock = {
    state: "OK",
    permission: null,
    reason_code: null,
    mtta: clock(1500, 22),
    mttr: clock(93600, 9),
    coverage: FULL_COVERAGE,
  };
  const noise_by_detector: ReportNoiseBlock = {
    state: "OK",
    permission: null,
    reason_code: null,
    rows: [
      { family: "log_source", anomaly_type: "VOLUME_SPIKE", judged: 16, noise: 4, ratio: 0.25, state: "OK" },
      { family: "waf", anomaly_type: "WAF_REQUEST_RATE_SPIKE", judged: 3, noise: 1, ratio: null, state: "INSUFFICIENT_DATA" },
    ],
    coverage: FULL_COVERAGE,
  };
  return {
    period: "last_7d",
    start: START,
    end: END,
    generated_at: END,
    alerts,
    anomalies,
    anomaly_clocks,
    noise_by_detector,
    ...over,
  };
}

export const partialWaf = (): ReportFamilyCoverage[] =>
  FULL_COVERAGE.map((c) =>
    c.family === "waf" ? { ...c, state: "PARTIAL", covered_from: "2026-11-06T00:00:00Z" } : c,
  );
