// Evaluation-fault copy and state for the source inventory, source page and
// the Dashboard log-source widgets.
//
// A bucket the engine did not judge is not a zero, not NORMAL, and not an
// incident. These helpers name that gap so the pages cannot pair the newest
// collected EPS with the last verdict as if they agreed.

import type { BehaviorSummary, SourceBehavior, SourceEvaluation } from "@/lib/api";
import { ENGLISH_BEHAVIOR, grouped, plainCount, type BehaviorI18n } from "@/lib/behaviorI18n";

export function evaluationOf(source: SourceBehavior): SourceEvaluation | null {
  return source.evaluation ?? null;
}

export function isRetrying(source: SourceBehavior): boolean {
  const ev = evaluationOf(source);
  return Boolean(
    ev && ev.failed_bucket_start && ev.given_up_at == null && ev.resolved_at == null,
  );
}

export function isGivenUp(source: SourceBehavior): boolean {
  const ev = evaluationOf(source);
  const skipped = source.skipped_buckets_in_window;
  return Boolean(
    (ev && ev.given_up_at) || (typeof skipped === "number" && skipped > 0),
  );
}

export function isClearedFault(source: SourceBehavior): boolean {
  const ev = evaluationOf(source);
  return Boolean(ev && ev.resolved_at && ev.given_up_at == null);
}

/** True when the displayed EPS comes from a bucket newer than the last verdict. */
export function figureNewerThanVerdict(source: SourceBehavior): boolean {
  const ev = evaluationOf(source);
  if (!ev?.judged_through || !source.last_bucket_at) return false;
  const figure = Date.parse(source.last_bucket_at);
  const judged = Date.parse(ev.judged_through);
  return Number.isFinite(figure) && Number.isFinite(judged) && figure > judged;
}

export function skippedInWindow(source: SourceBehavior): number | null {
  return source.skipped_buckets_in_window ?? null;
}

export function windowIsPartial(source: SourceBehavior): boolean {
  const skipped = skippedInWindow(source);
  return (
    isRetrying(source) ||
    (skipped != null && skipped > 0) ||
    figureNewerThanVerdict(source)
  );
}

function attemptsPhrase(attempts: number | null, i18n: BehaviorI18n): string {
  if (attempts == null) return i18n.t("behavior.fault.attempts.unknown");
  if (attempts === 1) return i18n.t("behavior.fault.attempts.once");
  return i18n.t("behavior.fault.attempts.times", { count: plainCount(attempts, i18n.locale) });
}

export function rowQualifierKind(
  source: SourceBehavior,
): "retrying" | "given_up" | null {
  if (isRetrying(source)) return "retrying";
  if (isGivenUp(source)) return "given_up";
  return null;
}

export type RowQualifierModel =
  | {
      kind: "retrying";
      judgedThrough: string | null;
      /** Already phrased; null when the window holds no given-up buckets. */
      givenUpSuffix: string | null;
    }
  | {
      kind: "given_up";
      /** Null when the count is not known; never invented. */
      count: number | null;
      latestBucket: string | null;
    };

function givenUpSuffix(source: SourceBehavior, i18n: BehaviorI18n): string | null {
  const count = source.skipped_buckets_in_window;
  if (typeof count === "number") {
    if (count <= 0) return null;
    return i18n.t("behavior.fault.givenUp.count", { count, shown: grouped(count, i18n.locale) });
  }
  if ((source.skipped_buckets?.length ?? 0) > 0) {
    return i18n.t("behavior.fault.givenUp.some");
  }
  return null;
}

function latestSkippedBucket(source: SourceBehavior): string | null {
  return (
    source.skipped_buckets?.[0]?.bucket_start ??
    source.evaluation?.failed_bucket_start ??
    null
  );
}

/** Inventory-row copy. Retrying keeps "since"; a given-up gap after resume does not. */
export function rowQualifierModel(
  source: SourceBehavior,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): RowQualifierModel | null {
  const kind = rowQualifierKind(source);
  if (kind == null) return null;
  if (kind === "retrying") {
    return {
      kind: "retrying",
      judgedThrough: source.evaluation?.judged_through ?? null,
      givenUpSuffix: givenUpSuffix(source, i18n),
    };
  }
  return {
    kind: "given_up",
    count: source.skipped_buckets_in_window ?? null,
    latestBucket: latestSkippedBucket(source),
  };
}

export function retryingNotice(source: SourceBehavior): {
  failedBucket: string;
  lastFailure: string | null;
  errorClass: string | null;
  attempts: number | null;
  maxAttempts: number | null;
  judgedThrough: string | null;
} | null {
  const ev = evaluationOf(source);
  if (!isRetrying(source) || !ev?.failed_bucket_start) return null;
  return {
    failedBucket: ev.failed_bucket_start,
    lastFailure: ev.last_failure_at,
    errorClass: ev.error_class,
    attempts: ev.attempts,
    maxAttempts: ev.max_attempts,
    judgedThrough: ev.judged_through,
  };
}

export function givenUpNotice(source: SourceBehavior): {
  failedBucket: string;
  lastFailure: string | null;
  errorClass: string | null;
  attempts: number | null;
  skipped: number | null;
  judgedThrough: string | null;
  resumed: boolean;
} | null {
  const ev = evaluationOf(source);
  if (!isGivenUp(source) || !ev?.failed_bucket_start) return null;
  const skipped = skippedInWindow(source);
  const resumed =
    ev.judged_through != null &&
    Date.parse(ev.judged_through) > Date.parse(ev.failed_bucket_start);
  return {
    failedBucket: ev.failed_bucket_start,
    lastFailure: ev.last_failure_at,
    errorClass: ev.error_class,
    attempts: ev.attempts,
    skipped,
    judgedThrough: ev.judged_through,
    resumed,
  };
}

export function clearedFaultLine(source: SourceBehavior): {
  failedBucket: string;
  attempts: number | null;
  errorClass: string | null;
  lastFailure: string | null;
  resolvedAt: string;
} | null {
  const ev = evaluationOf(source);
  if (!isClearedFault(source) || !ev?.failed_bucket_start || !ev.resolved_at) return null;
  return {
    failedBucket: ev.failed_bucket_start,
    attempts: ev.attempts,
    errorClass: ev.error_class,
    lastFailure: ev.last_failure_at,
    resolvedAt: ev.resolved_at,
  };
}

export function attemptsUsed(
  attempts: number | null,
  maxAttempts: number | null,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): string | null {
  if (attempts == null || maxAttempts == null) return null;
  return i18n.t("behavior.fault.attempts.used", {
    attempts: plainCount(attempts, i18n.locale),
    max: plainCount(maxAttempts, i18n.locale),
  });
}

export function attemptsFailed(attempts: number | null, i18n: BehaviorI18n = ENGLISH_BEHAVIOR): string {
  return attemptsPhrase(attempts, i18n);
}

export type EvaluationFaultQualifier = {
  /** Visible on the card: source count only. */
  text: string;
  /** Tooltip and accessible name: source count and unjudged-bucket count. */
  detail: string;
};

/**
 * Dashboard copy for sources the engine did not judge.
 *
 * The card names sources only. A given-up bucket always counts its source, so
 * a bucket count is never shown without a source count. Both counts belong in
 * `detail` (title and accessible name).
 */
export function evaluationFaultQualifier(
  summary: BehaviorSummary,
  i18n: BehaviorI18n = ENGLISH_BEHAVIOR,
): EvaluationFaultQualifier | null {
  const sources = summary.sources_with_evaluation_faults ?? 0;
  if (sources === 0) return null;
  const buckets = summary.buckets_not_judged ?? 0;
  const sourceCount = { count: sources, shown: grouped(sources, i18n.locale) };
  const sourceClause = i18n.t("behavior.fault.qualifier.sourcesClause", sourceCount);
  const bucketClause = i18n.t("behavior.fault.qualifier.bucketsClause", {
    count: buckets,
    shown: grouped(buckets, i18n.locale),
  });
  return {
    text: i18n.t("behavior.fault.qualifier.text", sourceCount),
    detail:
      buckets > 0
        ? `${sourceClause}; ${bucketClause}`
        : sourceClause,
  };
}
