// The pure part of chart.property_watch (Phase G §G.13.5): which state the widget is in before it
// reads any value, how it picks the values it draws, and when a last bucket counts as old. No
// React and no fetch, so each rule has a unit test.

import { formatCoveragePercent, statusReason } from "@/components/propertywatch/pwFormat";
import { historyProgress } from "@/components/propertywatch/history";
import { PW_MAX_SERIES } from "@/components/propertywatch/pwSeries";
import type { PropertyWatch, PropertyWatchCatalog, PwValue } from "@/lib/api";
import type { WidgetState } from "./types";

/**
 * The cadence of the PROPERTY_WATCH search family when a read does not carry one. It is the
 * family's default (1 h, `/admin/search-cadence`); a watch read always carries the real value in
 * `cadence_seconds`, so this is only the guard for a value that is missing or not positive.
 */
export const PW_DEFAULT_CADENCE_SECONDS = 3600;

/**
 * A last bucket older than this is shown with its time and age: twice the family cadence the
 * watch read carries (design §G.13.5), so an 8-hour family is not called stale after 2 hours.
 * The backend's own `STALE` status marks it as well; either one is enough.
 */
export function staleAfterSeconds(cadenceSeconds: number | null | undefined): number {
  const cadence =
    typeof cadenceSeconds === "number" && Number.isFinite(cadenceSeconds) && cadenceSeconds > 0
      ? cadenceSeconds
      : PW_DEFAULT_CADENCE_SECONDS;
  return 2 * cadence;
}

/** Statuses in which no bucket is being collected for a reason other than the watch's own state. */
const NOT_COLLECTED_STATUSES: ReadonlySet<string> = new Set([
  "NOT_ENABLED",
  "SCOPE_STALE",
  "SCOPE_TOO_LARGE",
  "SCOPE_EMPTY",
]);

/**
 * The state the watch and the switches decide alone, or null when values must be read to go on.
 * `empty`, `no_data` and `not_collected` carry the reason in words; none of them is a zero.
 */
export function earlyState(
  watch: PropertyWatch,
  catalog: Pick<PropertyWatchCatalog, "collection_enabled"> | null,
): WidgetState<never> | null {
  if (catalog && !catalog.collection_enabled) {
    return {
      kind: "not_collected",
      reason:
        "PW_COLLECTION_ENABLED is false, so no search is sent for any watch. Nothing new is collected, and no earlier bucket is drawn here. Open the watch for its history.",
    };
  }
  if (watch.status !== null && NOT_COLLECTED_STATUSES.has(watch.status)) {
    const setting =
      watch.status === "NOT_ENABLED"
        ? "PW_COLLECTION_ENABLED is false, so no search is sent for any watch. "
        : "";
    return {
      kind: "not_collected",
      reason: `${setting}${statusReason(watch.status, watch.status_reason)} Open the watch for its history.`,
    };
  }
  if (watch.history.state === "READING") {
    return {
      kind: "empty",
      reason: `Reading history from QRadar: ${historyProgress(watch.history)}. Nothing is judged until it finishes.`,
    };
  }
  if (watch.coverage === null) {
    const why = !watch.enabled
      ? "The watch is disabled, so no search is sent for it."
      : watch.status === null
        ? "It has not run yet: the first collection comes at the next cadence slot."
        : statusReason(watch.status, watch.status_reason);
    return {
      kind: "empty",
      reason: `No bucket has been collected for this watch yet. ${why} Created {at}.`,
      at: watch.created_at,
    };
  }
  if (watch.status === "NOT_APPLICABLE") {
    const c = watch.coverage;
    const events =
      c.total_events !== null && c.parsed_events !== null
        ? ` (${c.parsed_events.toLocaleString("en-US")} of ${c.total_events.toLocaleString("en-US")} events in scope)`
        : "";
    return {
      kind: "no_data",
      reason: `The bucket of {at} was collected, but the property parsed on no event${events}: coverage ${formatCoveragePercent(c.ratio)}. No value is measured and none is judged.`,
      at: c.bucket_start,
    };
  }
  return null;
}

/**
 * The values to draw: the pinned ones, then the values with the most events in the last bucket,
 * at most five. A value with no counted reading is never ranked as if it had one.
 */
export function widgetSelection(items: PwValue[]): string[] {
  const pinned = items.filter((v) => v.pinned);
  const counted = items
    .filter((v) => !v.pinned && v.events !== null && v.events > 0)
    .sort((a, b) => (b.events as number) - (a.events as number) || a.id.localeCompare(b.id));
  return [...pinned, ...counted].slice(0, PW_MAX_SERIES).map((v) => v.id);
}

/** Seconds since the last bucket started, or null when it is unknown or in the future. */
export function ageSeconds(lastBucket: string | null, now: Date): number | null {
  if (!lastBucket) return null;
  const then = Date.parse(lastBucket);
  if (Number.isNaN(then)) return null;
  const age = Math.round((now.getTime() - then) / 1000);
  return age < 0 ? null : age;
}

/** True when the backend says STALE, or the last bucket is older than twice the cadence. */
export function isStale(
  watch: Pick<PropertyWatch, "status" | "cadence_seconds">,
  age: number | null,
): boolean {
  return watch.status === "STALE" || (age !== null && age > staleAfterSeconds(watch.cadence_seconds));
}
