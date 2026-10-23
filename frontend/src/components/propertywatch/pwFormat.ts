// How property watches, their statuses and their readings are named on screen.
//
// The rule of the whole feature, in words: a count that was not measured is never a zero. A
// reading therefore never formats to a bare number unless it is one: a lower bound reads "at
// least n", an unknown reads "unknown", a value that was not tracked reads as that. The labels
// live beside the property-watch components, as the WAF and IPS ones do, so the Phase A
// vocabulary stays untouched.

import type {
  AnomalySummary,
  Cadence,
  MeasurementKind,
  PropertyWatch,
  PwAnomalyTypeName,
  PwDetectionDetail,
  PwBucket,
  PwReadingLabel,
  PwScopeKind,
  PwValue,
  PwWatchStatus,
} from "@/lib/api";
import { delayText, latencyCadence, latencyParts, type LatencyCadence, type LatencyParts } from "@/components/cadence/CadenceLatency";
import { NOT_MEASURED } from "@/lib/behavior";
import type { Tone } from "@/lib/health";
import { formatCount } from "@/lib/format";

/** Said once per page: the cadence is not a promise of speed. */
export function pwLatency(cadence?: LatencyCadence | null): LatencyParts {
  return latencyParts(
    "Watches",
    cadence,
    "This is not a seconds-level correlation engine — for alerting within seconds, use QRadar's own rules.",
  );
}

/** The latency sentence from an API cadence, or the figure-free one when the API sent none. */
export function pwLatencyFrom(cadence: Cadence | null | undefined): LatencyParts {
  return pwLatency(latencyCadence(cadence));
}

/**
 * When a new pin can be used in a rule. The number follows only when the cadence is known;
 * otherwise nothing is promised beyond "the next collection".
 */
export function pinAvailability(cadence?: LatencyCadence | null): string {
  const delay = delayText(cadence);
  return delay
    ? `after the next property-watch collection, which runs ${delay} after the events arrive`
    : "after the next property-watch collection";
}

// --- statuses ------------------------------------------------------------------

const STATUS_LABELS: Record<PwWatchStatus, string> = {
  NOT_ENABLED: "Collection off",
  DISABLED: "Disabled",
  SCOPE_EMPTY: "Scope empty",
  SCOPE_STALE: "Scope stale",
  SCOPE_TOO_LARGE: "Scope too large",
  NOT_APPLICABLE: "Not applicable",
  STALE: "Stale",
  READING_HISTORY: "Reading history",
  OPEN: "Incident open",
  RECOVERING: "Recovering",
  OK: "OK",
};

/** What each status means when the API sent no reason of its own. Never overrides one. */
const STATUS_MEANING: Record<PwWatchStatus, string> = {
  NOT_ENABLED: "The property-watch collection switch is off, so no search is sent.",
  DISABLED: "This watch is switched off. Its history and incidents are kept.",
  SCOPE_EMPTY: "The scope resolves to no synced log source, so no bucket is collected.",
  SCOPE_STALE:
    "The synced inventory of the scope is older than the staleness limit, so no bucket is collected and open incidents hold.",
  SCOPE_TOO_LARGE:
    "The scope needs more searches per run than one watch may use, so no bucket is collected.",
  NOT_APPLICABLE:
    "The property parsed on no event in scope. The values are not measured and no verdict is given.",
  STALE: "No bucket has been stored for longer than the cadence allows.",
  READING_HISTORY: "Reading history from QRadar. Nothing is judged until it finishes.",
  OPEN: "An incident is open on one of its values.",
  RECOVERING: "An incident is recovering and is held open until it is confirmed.",
  OK: "The last collected bucket was judged.",
};

export const NEVER_RUN_LABEL = "Never run";
export const NEVER_RUN_MEANING =
  "No bucket has been collected for this watch yet. A watch that has not run is not judged.";

export function statusLabel(status: PwWatchStatus | null): string {
  return status === null ? NEVER_RUN_LABEL : STATUS_LABELS[status];
}

/** The API's own reason when there is one, else the status' fixed meaning. */
export function statusReason(status: PwWatchStatus | null, reason: string | null): string {
  if (reason) return reason;
  return status === null ? NEVER_RUN_MEANING : STATUS_MEANING[status];
}

/**
 * The tone of a status pill. Only a genuinely judged outcome takes the positive tone; a watch
 * that is off, empty or not judged is neutral, and one that cannot collect is a warning.
 */
export function statusTone(status: PwWatchStatus | null): Tone | "info" {
  switch (status) {
    case "OK":
      return "ok";
    case "OPEN":
      return "crit";
    case "STALE":
    case "READING_HISTORY":
    case "NOT_APPLICABLE":
    case "SCOPE_STALE":
    case "SCOPE_TOO_LARGE":
    case "SCOPE_EMPTY":
    case "RECOVERING":
      return "warn";
    default:
      return "";
  }
}

/** Statuses an analyst has to look at: the list sorts these first (§G.13.2). */
const ATTENTION: PwWatchStatus[] = [
  "OPEN",
  "STALE",
  "NOT_APPLICABLE",
  "SCOPE_STALE",
  "SCOPE_TOO_LARGE",
];

export function needsAttention(status: PwWatchStatus | null): boolean {
  return status !== null && ATTENTION.includes(status);
}

/** Attention rows first in the order above, then the rest by name; stable and total. */
export function sortWatches<T extends Pick<PropertyWatch, "status" | "name" | "id">>(
  watches: T[],
): T[] {
  const rank = (w: T) => {
    const i = w.status === null ? -1 : ATTENTION.indexOf(w.status);
    return i < 0 ? ATTENTION.length : i;
  };
  return [...watches].sort(
    (a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id),
  );
}

// --- detectors, scope, properties ----------------------------------------------

const DETECTOR_LABELS: Record<PwAnomalyTypeName, string> = {
  PW_VALUE_SPIKE: "Value spike",
  PW_VALUE_DROP: "Value drop",
  PW_VALUE_DISAPPEARED: "Value disappeared",
  PW_NEW_VALUE: "New value",
  PW_NEW_COMBINATION: "New combination",
};

export const PW_TYPES = Object.keys(DETECTOR_LABELS) as PwAnomalyTypeName[];

export function isPwType(type: string | null | undefined): boolean {
  return type != null && type in DETECTOR_LABELS;
}

/** A property-watch detector's name; a type this build does not know shows as sent. */
export function pwTypeLabel(type: string): string {
  return DETECTOR_LABELS[type as PwAnomalyTypeName] ?? type;
}

export function isPwRow(a: Pick<AnomalySummary, "subject_kind" | "pw_value">): boolean {
  return a.subject_kind === "PW_VALUE" && a.pw_value != null;
}

const SCOPE_KIND_LABELS: Record<PwScopeKind, string> = {
  LOG_SOURCE: "Log source",
  LOG_SOURCE_TYPE: "Log source type",
  LOG_SOURCE_GROUP: "Log source group",
};

export function scopeKindLabel(kind: PwScopeKind): string {
  return SCOPE_KIND_LABELS[kind] ?? kind;
}

/**
 * The scope in words: `Log source group: DMZ firewalls`. A scope the inventory no longer lists
 * says so with its QRadar id, rather than a blank.
 */
export function scopeText(watch: Pick<PropertyWatch, "scope">): string {
  const { kind, name, qradar_id: id } = watch.scope;
  const label = scopeKindLabel(kind);
  if (name) return `${label}: ${name}`;
  return id === null
    ? `${label}: not in the synced inventory`
    : `${label} ${id}: not in the synced inventory`;
}

/** `Username × Action`. Property names are text and are rendered as text. */
export function propertiesText(watch: Pick<PropertyWatch, "properties">): string {
  return watch.properties.map((p) => p.name).join(" × ");
}

// --- readings ------------------------------------------------------------------

/** The unit of a bucket count, spoken from the bucket width: `events per 5 min`. */
export function eventsUnit(bucketSeconds: number | null | undefined): string {
  if (!bucketSeconds || bucketSeconds <= 0) return "events per bucket";
  const minutes = bucketSeconds / 60;
  return Number.isInteger(minutes) ? `events per ${minutes} min` : `events per ${bucketSeconds} s`;
}

const count = (n: number) => formatCount(n);

/** `12`, or `at least 12` for a lower bound. A missing count is an em dash, never `0`. */
export function formatEvents(
  events: number | null | undefined,
  measurement?: MeasurementKind | string | null,
): string {
  if (events == null || Number.isNaN(events)) return NOT_MEASURED;
  return measurement === "LOWER_BOUND" ? `at least ${count(events)}` : count(events);
}

/**
 * A value's reading in words. Only `COUNT` and `MEASURED_ZERO` are numbers; an unknown is not
 * a zero and a value that was not tracked at the bucket is not an observation at all.
 */
export function readingText(
  reading: PwReadingLabel,
  events: number | null,
  measurement: MeasurementKind | null,
): string {
  switch (reading) {
    case "COUNT":
      // A count with no measurement cannot be said to be exact: it is not shown as one.
      return events === null || measurement === null ? NOT_MEASURED : formatEvents(events, measurement);
    case "MEASURED_ZERO":
      return events === 0 ? "0" : NOT_MEASURED;
    case "UNKNOWN":
      return "unknown";
    case "NOT_AN_OBSERVATION":
      return "not tracked then";
    case "NO_BUCKET":
      return "not collected";
    default:
      return NOT_MEASURED;
  }
}

/** What a reading means, for the cell's title and assistive technology. */
export function readingMeaning(reading: PwReadingLabel): string {
  switch (reading) {
    case "COUNT":
      return "Events counted for this value in the bucket.";
    case "MEASURED_ZERO":
      return "Measured: the bucket was collected in full and this value had no events.";
    case "UNKNOWN":
      return "The bucket was a lower bound and this value was absent from it: not a zero, unknown.";
    case "NOT_AN_OBSERVATION":
      return "This value was not tracked at that bucket, so there is no observation.";
    case "NO_BUCKET":
      return "No bucket was collected, so there is no observation.";
    default:
      return "";
  }
}

// --- coverage and time ---------------------------------------------------------

/** A fraction as a whole percent, `96 %`; `null` is an em dash, never `0 %`. */
export function formatCoveragePercent(ratio: number | null | undefined): string {
  if (ratio == null || Number.isNaN(ratio)) return NOT_MEASURED;
  return `${Math.round(ratio * 100)} %`;
}

/** Coverage of the last bucket: `96 %`, or `at least 88 %` when the grouped search was cut. */
export function coverageText(coverage: PropertyWatch["coverage"]): string {
  if (!coverage || coverage.ratio === null) return NOT_MEASURED;
  const text = formatCoveragePercent(coverage.ratio);
  return coverage.measurement === "LOWER_BOUND" ? `at least ${text}` : text;
}

export { relativeAge } from "@/lib/time";

// --- the values table -----------------------------------------------------------

/** The share of parsed events a value held in the latest bucket, only when it bounds something. */
export function shareOfParsed(value: PwValue, latest: PwBucket | null): number | null {
  if (!latest || value.latest_bucket_start !== latest.bucket_start) return null;
  if (value.measurement !== "EXACT" || value.events === null) return null;
  if (value.reading !== "COUNT" && value.reading !== "MEASURED_ZERO") return null;
  // A share over a lower-bound total bounds nothing (design §G.8.1).
  if (latest.values_measurement !== "EXACT" || !latest.totals_complete) return null;
  if (latest.parsed_events === null || latest.parsed_events <= 0) return null;
  return value.events / latest.parsed_events;
}

export { formatShare } from "@/lib/format";

/** The expected range for the value's hour of the week, or why there is none. */
export function expectedRangeText(value: PwValue): string {
  if (value.baseline_state !== "RELIABLE") {
    return value.baseline_state === "LEARNING" ? "learning" : "no baseline";
  }
  if (value.expected_low === null || value.expected_high === null) return "no range for this hour";
  const fmt = (n: number) => (Number.isInteger(n) ? count(n) : n.toFixed(1));
  return `${fmt(value.expected_low)} – ${fmt(value.expected_high)}`;
}

/** `Learning (12 of 168 cells)` or `Baseline reliable (168 of 168 cells)`. */
export function baselineText(value: PwValue): string {
  const cells = `${value.reliable_cells} of ${value.cells_total} cells`;
  switch (value.baseline_state) {
    case "RELIABLE":
      return `Baseline reliable (${cells})`;
    case "LEARNING":
      return `Learning (${cells})`;
    default:
      return "No baseline yet";
  }
}

// --- the detector's own figures, with what each counts ---------------------------
//
// The API declares no unit for the new-value figures (PwDetectionDetailOut), so each unit below
// is the one the detector's code gives it:
//   events_since_first_seen, observed_value  `_materiality` in app/property_watch/detectors.py
//       compares `events` with `new_value_min_events`, and writes "{events} events across
//       {buckets} buckets since first appearance": events.
//   buckets_with_traffic  the `buckets` of that same sentence: buckets.
//   history_buckets_verified, and baseline_sample_count of a new-value row  `history_buckets` is
//       the verified history's length, `sample_count=novelty.history_buckets`: buckets.
//   threshold of a new-value row  `float(thresholds.new_value_min_events)`: events.
//   threshold of a volume row  `thresholds.deviation_z`, compared with the robust z: a z-score,
//       which has no unit.
//   baseline_sample_count of a volume row  the cell's retained bucket readings
//       (`retained[key].append(float(reading.events))` in baseline.py): buckets.

export interface PwFigure {
  label: string;
  /** The figure; null is "not recorded", never 0. */
  value: number | null;
  /** What it counts. `count` only where the code does not settle it. */
  unit: string;
  /** Shown beside the figure when it is a lower bound. */
  lowerBound?: boolean;
}

const plural = (n: number | null, one: string, many: string) => (n === 1 ? one : many);

/** The figure with its unit, or "not recorded"; `at least` for a lower bound. */
export function figureText(f: PwFigure): string {
  if (f.value === null || Number.isNaN(f.value)) return "not recorded";
  const n = count(f.value);
  const unit = f.unit === "events" ? plural(f.value, "event", "events") : f.unit === "buckets" ? plural(f.value, "bucket", "buckets") : f.unit;
  return `${f.lowerBound ? "at least " : ""}${n}${unit ? ` ${unit}` : ""}`;
}

/** The new-value and new-combination figures, each with what it counts. */
export function newValueFigures(d: PwDetectionDetail, lowerBound: boolean): PwFigure[] {
  return [
    { label: "Events since first seen", value: d.events_since_first_seen, unit: "events", lowerBound },
    { label: "Buckets with traffic", value: d.buckets_with_traffic, unit: "buckets", lowerBound },
    { label: "Minimum to report", value: d.threshold, unit: "events" },
    { label: "History verified", value: d.history_buckets_verified, unit: "buckets" },
  ];
}
