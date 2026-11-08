// Pure helpers of the low-volume model view on `/behavior/noise/[id]` (SOC-17e-ui).
//
// A missing number is the em dash, never "0". The two 404s of the model route are told apart by
// their detail: the setting being off is a stated condition, an unknown source is an error.

import type { NoiseModelCell } from "@/lib/api";

export const MODEL_DISABLED_DETAIL = "low-volume model preview is not enabled";

export const MODEL_METRICS = ["event_count", "average_eps"] as const;
export type ModelMetric = (typeof MODEL_METRICS)[number];

export const MISSING = "—";

export type ModelFailure = "disabled" | "unknown" | "forbidden" | "expired" | "failed";

/** What a failed read means. `detail` is the response's detail string, when it had one. */
export function modelFailure(status: number | null, detail: string | undefined): ModelFailure {
  if (status === 404) return detail === MODEL_DISABLED_DETAIL ? "disabled" : "unknown";
  if (status === 403) return "forbidden";
  if (status === 401) return "expired";
  return "failed";
}

const DECIMAL = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const PRECISE = new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 });
const COUNT = new Intl.NumberFormat("en-US");

/** A measured number with at most two decimals; null is unmeasured. */
export function num(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? DECIMAL.format(value) : MISSING;
}

/** A small probability or threshold, up to four decimals. */
export function precise(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? PRECISE.format(value) : MISSING;
}

export function count(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? COUNT.format(value) : MISSING;
}

/** The weight on the cell's own median, as a whole percent. */
export function weightPercent(weight: number): string {
  return `${Math.round(weight * 100)} %`;
}

/** Buckets in the window below p on either tail; null while the model was not scored. */
export function tailTotal(cell: NoiseModelCell): number | null {
  if (cell.upper_tail_below === null || cell.lower_tail_below === null) return null;
  return cell.upper_tail_below + cell.lower_tail_below;
}

/** The shade of a scored cell: 0 none, 1 to 2, 3 to 9, 10 and more buckets below p. */
export function tailBin(total: number): 0 | 1 | 2 | 3 {
  if (total <= 0) return 0;
  if (total <= 2) return 1;
  if (total <= 9) return 2;
  return 3;
}

export function cellKey(cell: Pick<NoiseModelCell, "metric" | "weekday" | "hour">): string {
  return `${cell.metric}-${cell.weekday}-${cell.hour}`;
}

export function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

/** The cells of one metric, indexed by weekday and hour. */
export function cellIndex(cells: readonly NoiseModelCell[], metric: string): Map<string, NoiseModelCell> {
  const map = new Map<string, NoiseModelCell>();
  for (const c of cells) if (c.metric === metric) map.set(`${c.weekday}-${c.hour}`, c);
  return map;
}
