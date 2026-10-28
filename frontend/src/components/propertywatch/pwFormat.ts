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
import { formatDuration, NOT_MEASURED } from "@/lib/behavior";
import { wordOrValue } from "@/lib/behaviorI18n";
import type { Tone } from "@/lib/health";
import { formatCount, formatPercent } from "@/lib/format";
import { relativeAge as ageFrom } from "@/lib/time";
import { worstCaseDelay } from "@/lib/searchCadence";

import { ENGLISH_PW, type PwI18n } from "./pwText";

function localizedDelay(
  cadence: LatencyCadence | null | undefined,
  i18n: PwI18n,
): string | null {
  const english = delayText(cadence);
  if (!english || !cadence) return null;
  if (i18n.locale === "en") return english;
  const seconds = worstCaseDelay(
    cadence.cadenceSeconds,
    cadence.bucketSeconds,
    cadence.settleSeconds,
  ).seconds;
  return i18n.t("propertyWatch.format.latencyUpTo", {
    duration: formatDuration(seconds, i18n),
  });
}

/** Said once per page: the cadence is not a promise of speed. */
export function pwLatency(
  cadence?: LatencyCadence | null,
  i18n: PwI18n = ENGLISH_PW,
): LatencyParts {
  if (i18n.locale !== "en") {
    const delay = localizedDelay(cadence, i18n);
    return {
      lead: i18n.t(
        delay
          ? "propertyWatch.format.latencyKnownLead"
          : "propertyWatch.format.latencyUnknownLead",
        delay ? { delay } : undefined,
      ),
      tail: i18n.t("propertyWatch.format.latencyTail", {
        caveat: i18n.t("propertyWatch.format.latencyCaveat"),
      }),
    };
  }
  return latencyParts(
    i18n.t("propertyWatch.format.latencySubject"),
    cadence,
    i18n.t("propertyWatch.format.latencyCaveat"),
  );
}

/** The latency sentence from an API cadence, or the figure-free one when the API sent none. */
export function pwLatencyFrom(
  cadence: Cadence | null | undefined,
  i18n: PwI18n = ENGLISH_PW,
): LatencyParts {
  return pwLatency(latencyCadence(cadence), i18n);
}

/**
 * When a new pin can be used in a rule. The number follows only when the cadence is known;
 * otherwise nothing is promised beyond "the next collection".
 */
export function pinAvailability(
  cadence?: LatencyCadence | null,
  i18n: PwI18n = ENGLISH_PW,
): string {
  const delay = localizedDelay(cadence, i18n);
  return delay
    ? i18n.t("propertyWatch.format.pinAfterDelay", { delay })
    : i18n.t("propertyWatch.format.pinAfterNext");
}

// --- statuses ------------------------------------------------------------------

const WATCH_STATUSES: Record<PwWatchStatus, true> = {
  NOT_ENABLED: true,
  DISABLED: true,
  SCOPE_EMPTY: true,
  SCOPE_STALE: true,
  SCOPE_TOO_LARGE: true,
  NOT_APPLICABLE: true,
  STALE: true,
  READING_HISTORY: true,
  OPEN: true,
  RECOVERING: true,
  OK: true,
};

export function statusLabel(
  status: PwWatchStatus | null,
  i18n: PwI18n = ENGLISH_PW,
): string {
  return status === null
    ? i18n.t("propertyWatch.status.neverRun")
    : Object.hasOwn(WATCH_STATUSES, status)
      ? wordOrValue(i18n, "enums.propertyWatchStatus", status)
      : status;
}

/** The API's own reason when there is one, else the status' fixed meaning. */
export function statusReason(
  status: PwWatchStatus | null,
  reason: string | null,
  i18n: PwI18n = ENGLISH_PW,
): string {
  if (reason) return reason;
  return i18n.t(`propertyWatch.status.meaning.${status ?? "NEVER_RUN"}`);
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
  return type != null && Object.hasOwn(DETECTOR_LABELS, type);
}

/** A property-watch detector's name; a type this build does not know shows as sent. */
export function pwTypeLabel(type: string, i18n: PwI18n = ENGLISH_PW): string {
  return isPwType(type) ? wordOrValue(i18n, "enums.propertyWatchAnomalyType", type) : type;
}

export function isPwRow(a: Pick<AnomalySummary, "subject_kind" | "pw_value">): boolean {
  return a.subject_kind === "PW_VALUE" && a.pw_value != null;
}

const SCOPE_KINDS: Record<PwScopeKind, true> = {
  LOG_SOURCE: true,
  LOG_SOURCE_TYPE: true,
  LOG_SOURCE_GROUP: true,
};

export function scopeKindLabel(kind: PwScopeKind, i18n: PwI18n = ENGLISH_PW): string {
  if (!Object.hasOwn(SCOPE_KINDS, kind)) return kind;
  return wordOrValue(i18n, "propertyWatch.scopeKind", kind);
}

/**
 * The scope in words: `Log source group: DMZ firewalls`. A scope the inventory no longer lists
 * says so with its QRadar id, rather than a blank.
 */
export function scopeText(
  watch: Pick<PropertyWatch, "scope">,
  i18n: PwI18n = ENGLISH_PW,
): string {
  const { kind, name, qradar_id: id } = watch.scope;
  const label = scopeKindLabel(kind, i18n);
  if (name) return i18n.t("propertyWatch.format.scopeNamed", { kind: label, name });
  return id === null
    ? i18n.t("propertyWatch.format.scopeMissing", { kind: label })
    : i18n.t("propertyWatch.format.scopeMissingId", { kind: label, id });
}

/** `Username × Action`. Property names are text and are rendered as text. */
export function propertiesText(watch: Pick<PropertyWatch, "properties">): string {
  return watch.properties.map((p) => p.name).join(" × ");
}

// --- readings ------------------------------------------------------------------

/** The unit of a bucket count, spoken from the bucket width: `events per 5 min`. */
export function eventsUnit(
  bucketSeconds: number | null | undefined,
  i18n: PwI18n = ENGLISH_PW,
): string {
  if (!bucketSeconds || bucketSeconds <= 0) {
    return i18n.t("propertyWatch.format.eventsPerBucket");
  }
  const minutes = bucketSeconds / 60;
  return Number.isInteger(minutes)
    ? i18n.t("propertyWatch.format.eventsPerMinutes", { minutes })
    : i18n.t("propertyWatch.format.eventsPerSeconds", { seconds: bucketSeconds });
}

const count = (n: number, i18n: PwI18n) => formatCount(n, i18n.locale);

/** `12`, or `at least 12` for a lower bound. A missing count is an em dash, never `0`. */
export function formatEvents(
  events: number | null | undefined,
  measurement?: MeasurementKind | string | null,
  i18n: PwI18n = ENGLISH_PW,
): string {
  if (events == null || Number.isNaN(events)) return NOT_MEASURED;
  const value = count(events, i18n);
  return measurement === "LOWER_BOUND"
    ? i18n.t("propertyWatch.format.atLeast", { value })
    : value;
}

/**
 * A value's reading in words. Only `COUNT` and `MEASURED_ZERO` are numbers; an unknown is not
 * a zero and a value that was not tracked at the bucket is not an observation at all.
 */
export function readingText(
  reading: PwReadingLabel,
  events: number | null,
  measurement: MeasurementKind | null,
  i18n: PwI18n = ENGLISH_PW,
): string {
  switch (reading) {
    case "COUNT":
      // A count with no measurement cannot be said to be exact: it is not shown as one.
      return events === null || measurement === null
        ? NOT_MEASURED
        : formatEvents(events, measurement, i18n);
    case "MEASURED_ZERO":
      return events === 0 ? "0" : NOT_MEASURED;
    case "UNKNOWN":
      return i18n.t("propertyWatch.format.reading.UNKNOWN");
    case "NOT_AN_OBSERVATION":
      return i18n.t("propertyWatch.format.reading.NOT_AN_OBSERVATION");
    case "NO_BUCKET":
      return i18n.t("propertyWatch.format.reading.NO_BUCKET");
    default:
      return NOT_MEASURED;
  }
}

/** What a reading means, for the cell's title and assistive technology. */
export function readingMeaning(
  reading: PwReadingLabel,
  i18n: PwI18n = ENGLISH_PW,
): string {
  switch (reading) {
    case "COUNT":
      return i18n.t("propertyWatch.format.readingMeaning.COUNT");
    case "MEASURED_ZERO":
      return i18n.t("propertyWatch.format.readingMeaning.MEASURED_ZERO");
    case "UNKNOWN":
      return i18n.t("propertyWatch.format.readingMeaning.UNKNOWN");
    case "NOT_AN_OBSERVATION":
      return i18n.t("propertyWatch.format.readingMeaning.NOT_AN_OBSERVATION");
    case "NO_BUCKET":
      return i18n.t("propertyWatch.format.readingMeaning.NO_BUCKET");
    default:
      return "";
  }
}

// --- coverage and time ---------------------------------------------------------

/** A fraction as a whole percent, `96 %`; `null` is an em dash, never `0 %`. */
export function formatCoveragePercent(
  ratio: number | null | undefined,
  i18n: PwI18n = ENGLISH_PW,
): string {
  return formatPercent(ratio, null, 0, i18n.locale);
}

/** Coverage of the last bucket: `96 %`, or `at least 88 %` when the grouped search was cut. */
export function coverageText(
  coverage: PropertyWatch["coverage"],
  i18n: PwI18n = ENGLISH_PW,
): string {
  if (!coverage || coverage.ratio === null) return NOT_MEASURED;
  const text = formatCoveragePercent(coverage.ratio, i18n);
  return coverage.measurement === "LOWER_BOUND"
    ? i18n.t("propertyWatch.format.coverageAtLeast", { value: text })
    : text;
}

export function relativeAge(
  iso: string | null | undefined,
  now: Date | number,
  i18n: PwI18n = ENGLISH_PW,
): string {
  return ageFrom(iso, now, (key, values) => i18n.t(`format.${key}`, values));
}

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
export function expectedRangeText(value: PwValue, i18n: PwI18n = ENGLISH_PW): string {
  if (value.baseline_state !== "RELIABLE") {
    return value.baseline_state === "LEARNING"
      ? i18n.t("propertyWatch.format.learning")
      : i18n.t("propertyWatch.format.noBaseline");
  }
  if (value.expected_low === null || value.expected_high === null) {
    return i18n.t("propertyWatch.format.noRange");
  }
  const fmt = (n: number) =>
    Number.isInteger(n)
      ? count(n, i18n)
      : n.toLocaleString(i18n.locale === "tr" ? "tr-TR" : "en-US", {
          minimumFractionDigits: 1,
          maximumFractionDigits: 1,
        });
  return `${fmt(value.expected_low)} – ${fmt(value.expected_high)}`;
}

/** `Learning (12 of 168 cells)` or `Baseline reliable (168 of 168 cells)`. */
export function baselineText(value: PwValue, i18n: PwI18n = ENGLISH_PW): string {
  const cells = i18n.t("propertyWatch.format.cells", {
    reliable: formatCount(value.reliable_cells, i18n.locale),
    total: formatCount(value.cells_total, i18n.locale),
  });
  switch (value.baseline_state) {
    case "RELIABLE":
      return i18n.t("propertyWatch.format.baselineReliable", { cells });
    case "LEARNING":
      return i18n.t("propertyWatch.format.baselineLearning", { cells });
    default:
      return i18n.t("propertyWatch.format.baselineNone");
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

/** The figure with its unit, or "not recorded"; `at least` for a lower bound. */
export function figureText(f: PwFigure, i18n: PwI18n = ENGLISH_PW): string {
  if (f.value === null || Number.isNaN(f.value)) {
    return i18n.t("propertyWatch.format.notRecorded");
  }
  const n = count(f.value, i18n);
  const unit =
    f.unit === "events"
      ? i18n.t(`propertyWatch.format.${f.value === 1 ? "eventOne" : "eventMany"}`)
      : f.unit === "buckets"
        ? i18n.t(`propertyWatch.format.${f.value === 1 ? "bucketOne" : "bucketMany"}`)
        : f.unit;
  if (!unit) {
    return f.lowerBound ? i18n.t("propertyWatch.format.figureAtLeast", { value: n }) : n;
  }
  return i18n.t(
    f.lowerBound
      ? "propertyWatch.format.figureAtLeastWithUnit"
      : "propertyWatch.format.figureWithUnit",
    { value: n, unit },
  );
}

/** The new-value and new-combination figures, each with what it counts. */
export function newValueFigures(
  d: PwDetectionDetail,
  lowerBound: boolean,
  i18n: PwI18n = ENGLISH_PW,
): PwFigure[] {
  return [
    {
      label: i18n.t("propertyWatch.format.newValue.eventsSince"),
      value: d.events_since_first_seen,
      unit: "events",
      lowerBound,
    },
    {
      label: i18n.t("propertyWatch.format.newValue.trafficBuckets"),
      value: d.buckets_with_traffic,
      unit: "buckets",
      lowerBound,
    },
    {
      label: i18n.t("propertyWatch.format.newValue.minimum"),
      value: d.threshold,
      unit: "events",
    },
    {
      label: i18n.t("propertyWatch.format.newValue.historyVerified"),
      value: d.history_buckets_verified,
      unit: "buckets",
    },
  ];
}
