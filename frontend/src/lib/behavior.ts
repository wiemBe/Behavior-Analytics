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
import { ENGLISH_BEHAVIOR, fixed, grouped, wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { durationText, formatRatioLocale, formatSignedPercent } from "@/i18n/measures";
import type { FormatLocale } from "@/lib/format";
import type { Tone } from "@/lib/health";
import { formatDuration as englishDuration } from "@/lib/time";

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
  locale: FormatLocale = "en",
): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  return fixed(value, digits, locale);
}

export { formatCount } from "@/lib/format";

/** A ratio, suffixed with x. A null ratio means the comparison does not exist. */
export function formatRatio(value: number | null | undefined, locale: FormatLocale = "en"): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  return locale === "en" ? `${value.toFixed(2)}x` : formatRatioLocale(value, locale);
}

export { formatShare } from "@/lib/format";

/** A percentage delta already expressed in percent by the backend. */
export function formatPercentDelta(
  value: number | null | undefined,
  locale: FormatLocale = "en",
): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  if (locale !== "en") return formatSignedPercent(value, locale);
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}\u00A0%`;
}

/** A signed absolute change, so a drop is visibly a drop. */
export function formatDelta(value: number | null | undefined, locale: FormatLocale = "en"): string {
  if (value == null || Number.isNaN(value)) return NOT_MEASURED;
  const sign = value > 0 ? "+" : "";
  return `${sign}${grouped(value, locale)}`;
}

/** "3 h 20 min" / "3 sa 20 dk"; without a translator exactly `lib/time`'s English. */
export function formatDuration(
  totalSeconds: number | null | undefined,
  i18n?: BehaviorI18n,
): string {
  if (!i18n) return englishDuration(totalSeconds);
  return durationText((key, values) => i18n.t(`units.${key}`, values), totalSeconds);
}

// --- detectors -------------------------------------------------------------

/**
 * Phase A anomaly types named from `enums.anomalyType`. The other families name theirs in their
 * own helpers, and this set is not the anomaly-list filter (that one stays the three detectors
 * an analyst can select).
 */
const PHASE_A_TYPES = new Set([
  "VOLUME_SPIKE",
  "VOLUME_DROP",
  "NO_EVENTS",
  "PARSING_DEGRADATION",
  "UNKNOWN_EVENT_SPIKE",
  "TIMESTAMP_DELAY",
  "CARDINALITY_DROP",
  "COLLECTION_ERROR",
  "REPEATED_PAYLOAD",
]);

/**
 * The detector's name for display, from the `enums.anomalyType` table. An identifier this build
 * does not know is shown as-is rather than hidden or guessed at.
 */
export function detectorLabel(type: string, i18n: BehaviorI18n = ENGLISH_BEHAVIOR): string {
  return PHASE_A_TYPES.has(type) ? wordOrValue(i18n, "enums.anomalyType", type) : type;
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
export function stateMeaningAgainst(
  state: AnomalyStateOrNullLocal,
  band: BandObservation,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): string {
  return meaning(state, i18n, band);
}

/**
 * What a lifecycle state means, in a sentence, for a page that shows no band beside it. English;
 * `stateMeaningIn` takes the interface language. (Two functions, not an optional argument: this one
 * is passed to `Array.map`, which would hand it the index.)
 */
export function stateMeaning(state: AnomalyStateOrNullLocal): string {
  return meaning(state, ENGLISH_BEHAVIOR);
}

export function stateMeaningIn(state: AnomalyStateOrNullLocal, i18n: BehaviorI18n): string {
  return meaning(state, i18n);
}

function meaning(state: AnomalyStateOrNullLocal, i18n: BehaviorI18n, band?: BandObservation): string {
  const say = (key: string) => i18n.t(`behavior.state.meaning.${key}`);
  switch (state) {
    case "INSUFFICIENT_DATA":
    case "CANDIDATE":
    case "OPEN":
    case "RECOVERING":
    case "RESOLVED":
    case "SUPPRESSED":
      return say(state);
    case "NORMAL": {
      const place = band ? bandPlace(band) : "unspecified";
      if (place === "outside") return say("NORMAL_outside");
      if (place === "unknown") return say("NORMAL_unknown");
      return say("NORMAL_inside");
    }
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

/** What a dimension's availability means, in English; `dimensionMeaningIn` takes the language. */
export function dimensionMeaning(availability: DimensionAvailability | null | undefined): string {
  return dimensionMeaningIn(availability, ENGLISH_BEHAVIOR);
}

export function dimensionMeaningIn(
  availability: DimensionAvailability | null | undefined,
  i18n: BehaviorI18n,
): string {
  switch (availability) {
    case "AVAILABLE":
    case "TRUNCATED":
    case "UNAVAILABLE":
    case "FAILED":
      return i18n.t(`behavior.dimension.meaning.${availability}`);
    default:
      return "";
  }
}

/**
 * The explanation dimensions the backend requests, by the field name it sends. `qid` is a QRadar
 * term and reads the same in both languages, so it is not a catalog message.
 */
const QID_LABEL = "QID";

/** The label of a dimension; a field this build does not know is shown as the backend sent it. */
export function dimensionLabel(dimension: string, i18n: BehaviorI18n = ENGLISH_BEHAVIOR): string {
  if (dimension === "qid") return QID_LABEL;
  return wordOrValue(i18n, "behavior.dimension.label", dimension);
}

// --- buckets ---------------------------------------------------------------

export function completenessMeaning(
  completeness: BucketCompleteness | null | undefined,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): string {
  switch (completeness) {
    case "COMPLETE":
    case "PARTIAL":
    case "MISSING":
      return i18n.t(`behavior.bucket.meaning.${completeness}`);
    default:
      return "";
  }
}

// --- detector --------------------------------------------------------------

export function robustScoreMeaning(
  status: RobustScoreStatus | null | undefined,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): string {
  switch (status) {
    case "OK":
    case "DEGENERATE":
    case "UNAVAILABLE":
      return i18n.t(`behavior.score.meaning.${status}`);
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
