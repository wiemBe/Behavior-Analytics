// Derived cells of the anomaly list (SOC-1b: QW5 Age, QW8 percent change and "No events for").
//
// Every helper returns `null` when the figure is not measured. The list then prints a dash, never
// a zero: an anomaly with no start has no age, and a ratio the backend did not send has no change.

import type { AnomalySummary } from "@/lib/api";

/** Seconds since the anomaly started, or `null` when the start is missing or in the future. */
export function ageSeconds(
  a: Pick<AnomalySummary, "anomaly_start">,
  nowMs: number,
): number | null {
  if (!a.anomaly_start) return null;
  const start = Date.parse(a.anomaly_start);
  if (Number.isNaN(start) || start > nowMs) return null;
  const seconds = Math.floor((nowMs - start) / 1000);
  // Whole minutes once there is one: a ticking seconds counter is noise in a list.
  return seconds >= 60 ? seconds - (seconds % 60) : seconds;
}

/** The change against expected, in percent, from the backend's deviation ratio (observed / expected). */
export function changePercent(a: Pick<AnomalySummary, "deviation_ratio">): number | null {
  const r = a.deviation_ratio;
  if (r == null || !Number.isFinite(r)) return null;
  return (r - 1) * 100;
}

/** A NO_EVENTS row has no ratio: the finding is how long nothing arrived. */
export function isNoEvents(a: Pick<AnomalySummary, "anomaly_type" | "subject_kind">): boolean {
  return a.anomaly_type === "NO_EVENTS" && (a.subject_kind ?? "LOG_SOURCE") === "LOG_SOURCE";
}

/**
 * How long a NO_EVENTS anomaly has been silent: its recorded duration once it ended, otherwise
 * the time since it started. `null` when neither is known.
 */
export function silenceSeconds(
  a: Pick<AnomalySummary, "anomaly_start" | "duration_seconds">,
  nowMs: number,
): number | null {
  if (a.duration_seconds != null) return a.duration_seconds;
  return ageSeconds(a, nowMs);
}
