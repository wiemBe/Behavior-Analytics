// The one-line incident summary.
//
// Deterministic, and built only from values the API already returned. No model
// is involved: the summary appears at the top of an investigation page, and an
// analyst must be able to trust that it is a restatement of the evidence
// rather than a plausible-sounding paraphrase of it.
//
// Two things it never does:
//
//   * Claim causation. "The largest observed contributors were X and Y" is
//     supported by the contribution shares. "Caused by X" is not, and no
//     amount of contribution share makes it so.
//
//   * Speak for a dimension that was not collected. Only AVAILABLE and
//     TRUNCATED dimensions reach the sentence; an UNAVAILABLE one contributes
//     nothing, because nothing about it was observed.

import type { AnomalyDetail } from "@/lib/api";
import { formatMetric } from "@/lib/behavior";
import { ENGLISH_ANOMALIES } from "@/lib/anomaliesI18n";
import { plainCount, wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import {
  contributorDisplayValue,
  selectHeadlineContributors,
  type HeadlineContributor,
} from "@/lib/contributors";

export interface IncidentSummary {
  /** The summary sentence. Always present. */
  text: string;
  /** A stated limitation on the evidence behind it, or null. */
  caveat: string | null;
}

/**
 * How each dimension's value reads inside a sentence.
 *
 * "destination port 445" rather than "Destination port: 445" — the summary is
 * prose, and a label-colon-value pair reads as a form field.
 */
function phrase(pick: HeadlineContributor, i18n: BehaviorI18n): string {
  const value = contributorDisplayValue(pick.contributor);
  switch (pick.dimension) {
    case "event_name":
    case "qid":
      return i18n.t("anomalies.summary.phrase.traffic", { value });
    case "destination_port":
    case "source_port":
    case "source_ip":
    case "destination_ip":
    case "action":
    case "protocol":
    case "category":
    case "username":
      return i18n.t(`anomalies.summary.phrase.${pick.dimension}`, { value });
    default:
      return `${pick.dimensionLabel.toLocaleLowerCase(i18n.locale === "tr" ? "tr-TR" : "en-US")} ${value}`;
  }
}

/** "a, b and c" — an Oxford-comma-free list, because this is one sentence. */
function joinPhrases(parts: string[], i18n: BehaviorI18n): string {
  if (parts.length === 1) return parts[0];
  if (parts.length === 2) return i18n.t("anomalies.summary.join.two", { a: parts[0], b: parts[1] });
  return i18n.t("anomalies.summary.join.many", {
    head: parts.slice(0, -1).join(", "),
    last: parts[parts.length - 1],
  });
}

/** The volume clause: what the detector measured, in both directions. */
function volumeClause(anomaly: AnomalyDetail, i18n: BehaviorI18n): string {
  const expected = formatMetric(anomaly.expected_value, 2, i18n.locale);
  const observed = formatMetric(anomaly.observed_value, 2, i18n.locale);

  if (anomaly.anomaly_type === "NO_EVENTS") {
    // No observed value to state: the finding is the absence of one.
    return anomaly.expected_value != null
      ? i18n.t("anomalies.summary.volume.noEventsExpected", { expected })
      : i18n.t("anomalies.summary.volume.noEvents");
  }

  // A missing measurement is never described as a change from or to a number.
  if (anomaly.observed_value == null || anomaly.expected_value == null) {
    return i18n.t("anomalies.summary.volume.deviated");
  }

  const direction =
    anomaly.anomaly_type === "VOLUME_DROP" ||
    anomaly.observed_value < anomaly.expected_value
      ? "decreased"
      : "increased";

  return i18n.t(`anomalies.summary.volume.${direction}`, { expected, observed });
}

/** The evidence clause: what the collected dimensions showed, if anything. */
function contributorClause(
  anomaly: AnomalyDetail,
  picks: HeadlineContributor[],
  i18n: BehaviorI18n,
): string {
  if (picks.length === 0) {
    switch (anomaly.evidence_status) {
      case "PENDING":
        return i18n.t("anomalies.summary.evidence.pending");
      case "FAILED":
        return i18n.t("anomalies.summary.evidence.failed");
      case "NOT_REQUESTED":
        return i18n.t("anomalies.summary.evidence.notRequested");
      default:
        return i18n.t("anomalies.summary.evidence.unavailable");
    }
  }

  const parts = joinPhrases(
    picks.map((pick) => phrase(pick, i18n)),
    i18n,
  );
  // A drop's contributors are reductions. Calling them "contributors" without
  // saying so reads as though those values increased.
  const isDrop =
    anomaly.anomaly_type === "VOLUME_DROP" ||
    (anomaly.observed_value != null &&
      anomaly.expected_value != null &&
      anomaly.observed_value < anomaly.expected_value);

  const kind = isDrop ? "reduction" : "contributor";
  return i18n.t(
    `anomalies.summary.largest.${kind}.${picks.length === 1 ? "one" : "many"}`,
    { parts },
  );
}

/** The stated limitation on the evidence, where one applies. */
function caveatFor(
  anomaly: AnomalyDetail,
  picks: HeadlineContributor[],
  i18n: BehaviorI18n,
): string | null {
  if (anomaly.evidence_status === "PARTIAL") return i18n.t("anomalies.summary.caveat.partial");
  // Even on an otherwise complete package, a headline drawn from a capped
  // dimension is the top of a list rather than the whole of it.
  if (picks.some((p) => p.truncated)) return i18n.t("anomalies.summary.caveat.capped");
  return null;
}

/**
 * The deterministic summary for one anomaly.
 *
 * Same input, same sentence, every time — it is a pure function of the API
 * response and holds no clock, no randomness and no model.
 */
export function summarizeAnomaly(
  anomaly: AnomalyDetail,
  i18n: BehaviorI18n = ENGLISH_ANOMALIES,
): IncidentSummary {
  const picks = selectHeadlineContributors(anomaly.explanation_package, 3, i18n);
  return {
    text: `${volumeClause(anomaly, i18n)} ${contributorClause(anomaly, picks, i18n)}`,
    caveat: caveatFor(anomaly, picks, i18n),
  };
}

/**
 * A text description of the timeline, for readers who cannot see the chart.
 *
 * A chart with an aria-label says only that a chart is present. This states
 * what it shows: the range observed, what was expected, when the anomaly ran,
 * how it ended, and how much of the window was not collected — the last being
 * the fact a sighted reader gets from the shaded gaps.
 */
export function summarizeTimeline(
  {
    observed,
    expected,
    anomalyStart,
    anomalyEnd,
    state,
    incompleteBuckets,
    totalBuckets,
  }: {
    observed: (number | null)[];
    expected: number | null;
    anomalyStart: string | null;
    anomalyEnd: string | null;
    state: string;
    incompleteBuckets: number;
    totalBuckets: number;
  },
  i18n: BehaviorI18n = ENGLISH_ANOMALIES,
): string {
  const measured = observed.filter((v): v is number => v != null);

  if (measured.length === 0) return i18n.t("anomalies.summary.timeline.none");

  const metric = (value: number) => formatMetric(value, 2, i18n.locale);
  const parts: string[] = [
    i18n.t("anomalies.summary.timeline.range", {
      min: metric(Math.min(...measured)),
      max: metric(Math.max(...measured)),
      count: totalBuckets,
      shown: plainCount(totalBuckets, i18n.locale),
    }),
  ];

  if (expected != null) {
    parts.push(i18n.t("anomalies.summary.timeline.expected", { expected: metric(expected) }));
  }

  if (anomalyStart) {
    const stateText = wordOrValue(i18n, "enums.anomalyState", state);
    parts.push(
      anomalyEnd
        ? i18n.t("anomalies.summary.timeline.ran", {
            start: anomalyStart,
            end: anomalyEnd,
            state: stateText,
          })
        : i18n.t("anomalies.summary.timeline.running", { start: anomalyStart, state: stateText }),
    );
  }

  if (incompleteBuckets > 0) {
    // The gaps are the point: they are absent collection, not observed silence.
    parts.push(
      i18n.t("anomalies.summary.timeline.gaps", {
        count: incompleteBuckets,
        shown: plainCount(incompleteBuckets, i18n.locale),
      }),
    );
  }

  return parts.join(" ");
}
