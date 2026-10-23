import { describe, expect, it } from "vitest";

import type { SourceBehavior, SourceEvaluation } from "@/lib/api";
import { summary } from "@/lib/home/fixtures";

import {
  evaluationFaultQualifier,
  figureNewerThanVerdict,
  givenUpNotice,
  isClearedFault,
  isGivenUp,
  isRetrying,
  retryingNotice,
  rowQualifierKind,
  rowQualifierModel,
  windowIsPartial,
} from "./evaluationFault";

function evaluation(over: Partial<SourceEvaluation> = {}): SourceEvaluation {
  return {
    judged_through: "2026-10-22T17:05:00Z",
    failed_bucket_start: "2026-10-22T17:10:00Z",
    attempts: 2,
    max_attempts: 3,
    error_class: "RuntimeError",
    last_failure_at: "2026-10-22T19:52:00Z",
    given_up_at: null,
    resolved_at: null,
    ...over,
  };
}

function source(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "s-1",
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 50,
    expected_eps: 50,
    expected_low: 45,
    expected_high: 55,
    deviation_ratio: 1,
    state: "OPEN",
    baseline_sample_count: 30,
    baseline_completeness: 1,
    last_bucket_at: "2026-10-22T17:30:00Z",
    last_event_at: "2026-10-22T17:34:00Z",
    open_anomaly_count: 1,
    evaluation: evaluation(),
    skipped_buckets_in_window: 0,
    skipped_buckets: [],
    ...over,
  };
}

describe("retrying", () => {
  it("is retrying while given_up_at and resolved_at are both empty", () => {
    expect(isRetrying(source())).toBe(true);
    expect(isGivenUp(source())).toBe(false);
    expect(rowQualifierKind(source())).toBe("retrying");
  });

  it("names the failed bucket and the attempt bound", () => {
    const notice = retryingNotice(source());
    expect(notice?.failedBucket).toBe("2026-10-22T17:10:00Z");
    expect(notice?.attempts).toBe(2);
    expect(notice?.maxAttempts).toBe(3);
    expect(notice?.judgedThrough).toBe("2026-10-22T17:05:00Z");
  });
});

describe("given up", () => {
  const given = source({
    evaluation: evaluation({
      judged_through: "2026-10-22T17:30:00Z",
      attempts: 3,
      given_up_at: "2026-10-22T19:54:00Z",
    }),
    skipped_buckets_in_window: 1,
  });

  it("does not fold the source into a retrying or cleared state", () => {
    expect(isGivenUp(given)).toBe(true);
    expect(isRetrying(given)).toBe(false);
    expect(isClearedFault(given)).toBe(false);
    expect(rowQualifierKind(given)).toBe("given_up");
  });

  it("counts skipped buckets in the window and notes that evaluation resumed", () => {
    const notice = givenUpNotice(given);
    expect(notice?.skipped).toBe(1);
    expect(notice?.resumed).toBe(true);
  });

  it("leaves a missing skipped count as null instead of inventing 1", () => {
    const unknown = source({
      evaluation: evaluation({
        judged_through: "2026-10-22T17:30:00Z",
        attempts: 3,
        given_up_at: "2026-10-22T19:54:00Z",
      }),
      skipped_buckets_in_window: null,
    });
    expect(givenUpNotice(unknown)?.skipped).toBeNull();
  });
});

describe("cleared", () => {
  it("is a cleared fault when resolved_at is set and given_up_at is not", () => {
    const cleared = source({
      evaluation: evaluation({
        attempts: 1,
        given_up_at: null,
        resolved_at: "2026-10-22T19:53:00Z",
      }),
    });
    expect(isClearedFault(cleared)).toBe(true);
    expect(isRetrying(cleared)).toBe(false);
    expect(rowQualifierKind(cleared)).toBeNull();
  });
});

describe("the figure/verdict trap", () => {
  it("flags when observed EPS is from a bucket newer than judged_through", () => {
    expect(figureNewerThanVerdict(source())).toBe(true);
    expect(windowIsPartial(source())).toBe(true);
  });

  it("does not flag when the figure and the verdict are the same bucket", () => {
    const aligned = source({
      last_bucket_at: "2026-10-22T17:05:00Z",
      evaluation: evaluation({ failed_bucket_start: null, attempts: null }),
    });
    expect(figureNewerThanVerdict(aligned)).toBe(false);
  });

  it("never treats a missing figure as newer", () => {
    expect(figureNewerThanVerdict(source({ last_bucket_at: null }))).toBe(false);
  });
});

describe("the dashboard qualifier", () => {
  it("is silent when every source was judged", () => {
    expect(evaluationFaultQualifier(summary())).toBeNull();
  });

  it("names the source count on the card and both counts in the detail", () => {
    expect(
      evaluationFaultQualifier(
        summary({ sources_with_evaluation_faults: 2, buckets_not_judged: 3 }),
      ),
    ).toEqual({
      text: "Not judged: 2 sources (24 h)",
      detail:
        "2 sources have an evaluation fault; 3 buckets were not judged in 24 h",
    });
  });

  it("uses the singular when the count is one", () => {
    expect(
      evaluationFaultQualifier(
        summary({ sources_with_evaluation_faults: 1, buckets_not_judged: 1 }),
      ),
    ).toEqual({
      text: "Not judged: 1 source (24 h)",
      detail: "1 source has an evaluation fault; 1 bucket was not judged in 24 h",
    });
  });

  it("omits a zero bucket count from the detail and never shows buckets without a source", () => {
    expect(
      evaluationFaultQualifier(
        summary({ sources_with_evaluation_faults: 2, buckets_not_judged: 0 }),
      ),
    ).toEqual({
      text: "Not judged: 2 sources (24 h)",
      detail: "2 sources have an evaluation fault",
    });
    expect(
      evaluationFaultQualifier(
        summary({ sources_with_evaluation_faults: 0, buckets_not_judged: 1 }),
      ),
    ).toBeNull();
  });
});

describe("inventory row qualifier", () => {
  it("keeps Not judged since for a retrying fault", () => {
    expect(rowQualifierModel(source())).toEqual({
      kind: "retrying",
      judgedThrough: "2026-10-22T17:05:00Z",
      givenUpSuffix: null,
    });
  });

  it("appends the given-up count when a retrying window also holds them", () => {
    expect(
      rowQualifierModel(source({ skipped_buckets_in_window: 2 })),
    ).toEqual({
      kind: "retrying",
      judgedThrough: "2026-10-22T17:05:00Z",
      givenUpSuffix: "2 buckets given up in 24 h",
    });
  });

  it("omits the given-up count phrase when that count is not known", () => {
    expect(
      rowQualifierModel(
        source({
          skipped_buckets_in_window: null,
          skipped_buckets: [
            {
              bucket_start: "2026-10-22T16:40:00Z",
              error_class: "RuntimeError",
              attempts: 3,
              given_up_at: "2026-10-22T16:50:00Z",
              last_failure_at: "2026-10-22T16:50:00Z",
            },
          ],
        }),
      ),
    ).toEqual({
      kind: "retrying",
      judgedThrough: "2026-10-22T17:05:00Z",
      givenUpSuffix: "buckets given up in 24 h",
    });
  });

  it("names one given-up bucket after evaluation resumed, with no since", () => {
    const given = source({
      evaluation: evaluation({
        judged_through: "2026-10-22T17:30:00Z",
        attempts: 3,
        given_up_at: "2026-10-22T19:54:00Z",
      }),
      skipped_buckets_in_window: 1,
      skipped_buckets: [
        {
          bucket_start: "2026-10-22T17:10:00Z",
          error_class: "RuntimeError",
          attempts: 3,
          given_up_at: "2026-10-22T19:54:00Z",
          last_failure_at: "2026-10-22T19:54:00Z",
        },
      ],
    });
    expect(rowQualifierKind(given)).toBe("given_up");
    expect(rowQualifierModel(given)).toEqual({
      kind: "given_up",
      count: 1,
      latestBucket: "2026-10-22T17:10:00Z",
    });
  });

  it("names the latest of several given-up buckets from skipped_buckets[0]", () => {
    const given = source({
      evaluation: evaluation({
        judged_through: "2026-10-22T17:30:00Z",
        attempts: 3,
        given_up_at: "2026-10-22T19:54:00Z",
      }),
      skipped_buckets_in_window: 2,
      skipped_buckets: [
        {
          bucket_start: "2026-10-22T17:10:00Z",
          error_class: "RuntimeError",
          attempts: 3,
          given_up_at: "2026-10-22T19:54:00Z",
          last_failure_at: "2026-10-22T19:54:00Z",
        },
        {
          bucket_start: "2026-10-22T16:40:00Z",
          error_class: "RuntimeError",
          attempts: 3,
          given_up_at: "2026-10-22T16:50:00Z",
          last_failure_at: "2026-10-22T16:50:00Z",
        },
      ],
    });
    expect(rowQualifierModel(given)).toEqual({
      kind: "given_up",
      count: 2,
      latestBucket: "2026-10-22T17:10:00Z",
    });
  });

  it("omits the count phrase when the given-up count is not known", () => {
    const given = source({
      evaluation: evaluation({
        judged_through: "2026-10-22T17:30:00Z",
        attempts: 3,
        given_up_at: "2026-10-22T19:54:00Z",
      }),
      skipped_buckets_in_window: null,
      skipped_buckets: [
        {
          bucket_start: "2026-10-22T17:10:00Z",
          error_class: "RuntimeError",
          attempts: 3,
          given_up_at: "2026-10-22T19:54:00Z",
          last_failure_at: "2026-10-22T19:54:00Z",
        },
      ],
    });
    expect(rowQualifierModel(given)).toEqual({
      kind: "given_up",
      count: null,
      latestBucket: "2026-10-22T17:10:00Z",
    });
  });
});
