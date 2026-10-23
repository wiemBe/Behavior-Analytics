// Presentation rules for Phase A behavioral analytics.
//
// The single rule this file exists to enforce: a value that was never measured
// must never be rendered in the same visual language as a value that was
// measured and found to be zero. "This source sent 0 events" and "we did not
// observe this source" lead to opposite operator actions, and the difference
// between them is destroyed the moment a `null` is formatted as "0".
//
// The same applies to states. INSUFFICIENT_DATA is not NORMAL, and an
// UNAVAILABLE evidence dimension is not a clean one.

import type {
  AnomalyState,
  BucketCompleteness,
  DimensionAvailability,
  EvidenceStatus,
  RobustScoreStatus,
} from "@/lib/api";
import type { Tone } from "@/lib/health";

// Re-exported for components that only need the tone union.
export type { Tone };

type AnomalyStateOrNullLocal = AnomalyState | null | undefined;

/** The em dash used for "not measured". Never "0", never an empty cell. */
export const NOT_MEASURED = "—";

/**
 * Format a number that may not have been measured.
 *
 * `0` formats as "0" because it is a measurement; `null` and `undefined` format
 * as an em dash because they are not. This asymmetry is the whole point of the
 * function — `value || "—"` would collapse both cases and is the bug this
 * exists to prevent.
 */
export function formatMetric(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  return value.toFixed(digits);
}

export { formatCount } from "@/lib/format";

/** A ratio, suffixed with x. A null ratio means the comparison does not exist. */
export function formatRatio(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  return `${value.toFixed(2)}x`;
}

export { formatShare } from "@/lib/format";

/** A percentage delta already expressed in percent by the backend. */
export function formatPercentDelta(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}\u00A0%`;
}

/** A signed absolute change, so a drop is visibly a drop. */
export function formatDelta(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString("en-US")}`;
}

export { formatDuration } from "@/lib/time";

// --- detectors -------------------------------------------------------------

/** Detector identifiers as an analyst would say them. */
const DETECTOR_LABELS: Record<string, string> = {
  VOLUME_SPIKE: "Volume spike",
  VOLUME_DROP: "Volume drop",
  NO_EVENTS: "No events",
};

/**
 * The detector's name for display. An identifier this build does not know is
 * shown as-is rather than hidden or guessed at.
 */
export function detectorLabel(type: string): string {
  return DETECTOR_LABELS[type] ?? type;
}

// --- lifecycle -------------------------------------------------------------

/**
 * Tone for a lifecycle state.
 *
 * INSUFFICIENT_DATA is deliberately neutral rather than green: it means we
 * cannot yet judge the source, and painting it as healthy is precisely the
 * false assurance the Phase A design exists to prevent.
 */
export function stateTone(state: AnomalyStateOrNullLocal): Tone {
  switch (state) {
    case "NORMAL":
    case "RESOLVED":
      return "ok";
    case "OPEN":
      return "crit";
    case "CANDIDATE":
    case "RECOVERING":
      return "warn";
    case "INSUFFICIENT_DATA":
    case "SUPPRESSED":
    default:
      return "";
  }
}

/** Where the observed value sits in the band the page is showing beside the sentence. */
function bandPlace(band: BandObservation): "inside" | "outside" | "unknown" {
  const { observed, low, high } = band;
  if (
    observed === null ||
    low === null ||
    high === null ||
    !Number.isFinite(observed) ||
    !Number.isFinite(low) ||
    !Number.isFinite(high)
  ) {
    return "unknown";
  }
  return observed >= low && observed <= high ? "inside" : "outside";
}

/** The numbers shown beside a state sentence, when the page has them. */
export interface BandObservation {
  observed: number | null;
  low: number | null;
  high: number | null;
}

/**
 * What a lifecycle state means, in a sentence.
 *
 * A band is passed by a page that shows the observed value and the expected band beside the
 * sentence. A NORMAL verdict is worded "within the expected band" only when those numbers say
 * so. Outside the band, it names the detector's verdict and says the value is not inside.
 * With a number missing, it names the verdict and claims nothing about the band.
 */
export function stateMeaningAgainst(state: AnomalyStateOrNullLocal, band: BandObservation): string {
  return meaning(state, band);
}

/** What a lifecycle state means, in a sentence, for a page that shows no band beside it. */
export function stateMeaning(state: AnomalyStateOrNullLocal): string {
  return meaning(state);
}

function meaning(state: AnomalyStateOrNullLocal, band?: BandObservation): string {
  switch (state) {
    case "INSUFFICIENT_DATA":
      return "No adequate baseline for this seasonal cell yet — this source is not being judged, which is not the same as being healthy.";
    case "NORMAL": {
      const place = band ? bandPlace(band) : "unspecified";
      if (place === "outside") {
        return "NORMAL by the detector's verdict for this weekday and hour; the observed value shown here is not inside the expected band.";
      }
      if (place === "unknown") {
        return "NORMAL by the detector's verdict for this weekday and hour.";
      }
      return "Observed volume is within the expected band for this weekday and hour.";
    }
    case "CANDIDATE":
      return "One abnormal interval recorded. Not yet an incident: promotion requires consecutive abnormal intervals.";
    case "OPEN":
      return "Confirmed incident — enough consecutive abnormal intervals to rule out a single-bucket artefact.";
    case "RECOVERING":
      return "Back inside the expected band, but held open until enough consecutive normal intervals confirm it.";
    case "RESOLVED":
      return "Closed after sustained normal behavior.";
    case "SUPPRESSED":
      return "Detected while the source was in maintenance. Recorded for history, never notified.";
    default:
      return "";
  }
}

/** States that mean "we cannot yet judge", not "this is fine". */
export function isUnjudged(state: AnomalyStateOrNullLocal): boolean {
  return state === "INSUFFICIENT_DATA";
}

// --- evidence --------------------------------------------------------------

export function evidenceTone(status: EvidenceStatus | null | undefined): Tone {
  switch (status) {
    case "COMPLETE":
      return "ok";
    case "PARTIAL":
    case "PENDING":
      return "warn";
    case "FAILED":
      return "crit";
    // NOT_REQUESTED and UNAVAILABLE are neither good nor bad news: no evidence
    // was gathered, and the page must say so rather than colour it.
    case "NOT_REQUESTED":
    case "UNAVAILABLE":
    default:
      return "";
  }
}

export function dimensionTone(
  availability: DimensionAvailability | null | undefined,
): Tone {
  switch (availability) {
    case "AVAILABLE":
      return "ok";
    case "TRUNCATED":
      return "warn";
    case "FAILED":
      return "crit";
    case "UNAVAILABLE":
    default:
      return "";
  }
}

/**
 * Whether a dimension's contributor rows may be shown at all.
 *
 * A truncated result is still a real observation of the top of the list, so its
 * rows are shown; an unavailable or failed dimension has nothing to show.
 */
export function contributorsAreShowable(
  availability: DimensionAvailability | null | undefined,
): boolean {
  return availability === "AVAILABLE" || availability === "TRUNCATED";
}

/**
 * Whether the new/disappeared counts may be stated as findings.
 *
 * Only when the dimension was collected in full. Under truncation the counts
 * are an artifact of the value cap, not an observation: a value looks "new"
 * merely because it fell below the cap in the other window, and "disappeared"
 * for the same reason in reverse. Reporting those numbers as findings would
 * assert something the query never established, so they must not be rendered
 * as bare counts.
 */
export function newAndDisappearedAreDeterminate(
  availability: DimensionAvailability | null | undefined,
): boolean {
  return availability === "AVAILABLE";
}

export function dimensionMeaning(
  availability: DimensionAvailability | null | undefined,
): string {
  switch (availability) {
    case "AVAILABLE":
      return "Collected in full for both windows.";
    case "TRUNCATED":
      return "The result hit the configured value cap, so the contributors below are the top of the list rather than the whole of it. New and disappeared values cannot be determined under a cap — a value can look new simply because it fell below the cap in the other window — so those counts are not reported.";
    case "UNAVAILABLE":
      return "Unavailable — this field was not exposed by the QRadar event schema or DSM for the selected interval. It has not been checked, and no conclusion may be drawn from the absence of contributors here.";
    case "FAILED":
      return "The query for this dimension did not complete. It has not been checked.";
    default:
      return "";
  }
}

/** Human labels for the explanation dimensions the backend requests. */
const DIMENSION_LABELS: Record<string, string> = {
  qid: "QID",
  event_name: "Event name",
  source_ip: "Source IP",
  destination_ip: "Destination IP",
  destination_port: "Destination port",
  source_port: "Source port",
  username: "Username",
  action: "Action",
  category: "Category",
  protocol: "Protocol",
};

export function dimensionLabel(dimension: string): string {
  return DIMENSION_LABELS[dimension] ?? dimension;
}

// --- buckets ---------------------------------------------------------------

export function completenessMeaning(
  completeness: BucketCompleteness | null | undefined,
): string {
  switch (completeness) {
    case "COMPLETE":
      return "The whole interval was observed.";
    case "PARTIAL":
      return "Only part of this interval was collected, so its volume is an undercount rather than a measurement. It never enters a baseline or drives a verdict.";
    case "MISSING":
      return "This interval was not collected at all. Its absence says nothing about whether the source was sending events.";
    default:
      return "";
  }
}

// --- detector --------------------------------------------------------------

export function robustScoreMeaning(
  status: RobustScoreStatus | null | undefined,
): string {
  switch (status) {
    case "OK":
      return "MAD was non-zero, so the robust z-score is a meaningful measure of surprise and the verdict rests on it.";
    case "DEGENERATE":
      return "MAD was zero — more than half the baseline samples were identical, which is normal for a steady low-volume source. The z-score is therefore an artefact of the floored scale and was not used. The verdict rests on the deterministic expected-bound, ratio and absolute-delta tests instead, which is sound but weaker evidence, and the confidence is capped to reflect that.";
    case "UNAVAILABLE":
      return "No robust score could be computed for this verdict.";
    default:
      return "";
  }
}

/** True when the confidence figure carries a stated limitation. */
export function hasConfidenceLimitation(
  status: RobustScoreStatus | null | undefined,
): boolean {
  return status === "DEGENERATE" || status === "UNAVAILABLE";
}
