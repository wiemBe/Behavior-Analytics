import { describe, expect, it } from "vitest";

import { NO_RUN_RECORDED, wafCollection, wafSource } from "./fixtures";
import { collectionTone, isJudged, isOverflowing, needsAttention, sortByAttention, validationView } from "./wafSourceHealth";

describe("validationView", () => {
  it("calls a profile VALID only when it is valid for the current version", () => {
    expect(validationView(wafSource())).toMatchObject({ label: "VALID", tone: "ok" });
  });

  it("does not call a profile valid for an older version valid", () => {
    const v = validationView(
      wafSource({ profile_version: 5, validated_profile_version: 4, validation_current: false }),
    );
    expect(v.label).toBe("STALE");
    expect(v.tone).toBe("warn");
    expect(v.detail).toContain("v4");
    expect(v.detail).toContain("v5");
  });

  it("keeps UNVALIDATED neutral, not green and not red", () => {
    const v = validationView(
      wafSource({ validation_status: "UNVALIDATED", validation_current: false, validated_profile_version: null }),
    );
    expect(v.label).toBe("UNVALIDATED");
    expect(v.tone).toBe("");
  });

  it("marks INVALID as critical", () => {
    expect(validationView(wafSource({ validation_status: "INVALID", validation_current: false })).tone).toBe("crit");
  });
});

describe("judging and attention", () => {
  it("judges only an enabled, currently VALID source", () => {
    expect(isJudged(wafSource())).toBe(true);
    expect(isJudged(wafSource({ enabled: false }))).toBe(false);
    expect(isJudged(wafSource({ validation_current: false }))).toBe(false);
  });

  it("needs attention when not judged, overflowing, or at the ceiling", () => {
    expect(needsAttention(wafSource())).toBe(false);
    expect(needsAttention(wafSource({ enabled: false }))).toBe(true);
    expect(
      needsAttention(
        wafSource({ admission: { ...wafSource().admission, buckets_admission_truncated: 3 } }),
      ),
    ).toBe(true);
    expect(needsAttention(wafSource({ active_endpoints: 500 }))).toBe(true);
  });

  it("does not read a null overflow count as overflow", () => {
    const quiet = wafSource({
      admission: { buckets_in_range: 0, buckets_admission_truncated: null, rejected_over_quota: null, below_admission_threshold: null, untracked_requests: null },
    });
    expect(needsAttention(quiet)).toBe(false);
  });

  it("lists sources needing attention first, then by name", () => {
    const ok = wafSource({ log_source_id: "a", log_source_name: "alpha" });
    const bad = wafSource({ log_source_id: "z", log_source_name: "zulu", enabled: false });
    expect(sortByAttention([ok, bad]).map((s) => s.log_source_id)).toEqual(["z", "a"]);
  });
});

describe("isOverflowing", () => {
  const admission = (over: Partial<ReturnType<typeof wafSource>["admission"]>) =>
    wafSource({
      admission: {
        buckets_in_range: 10,
        buckets_admission_truncated: 0,
        rejected_over_quota: 0,
        below_admission_threshold: 0,
        untracked_requests: 0,
        ...over,
      },
    });

  it("counts quota refusals and admission truncation", () => {
    expect(isOverflowing(admission({ rejected_over_quota: 3 }))).toBe(true);
    expect(isOverflowing(admission({ buckets_admission_truncated: 2 }))).toBe(true);
  });

  it("never counts keys below the admission threshold, which are ordinary noise", () => {
    expect(isOverflowing(admission({ below_admission_threshold: 702 }))).toBe(false);
  });
});

describe("judging needs collection evidence (FIX-71e)", () => {
  it("does not judge a VALID enabled source nothing recorded a run for", () => {
    expect(isJudged(wafSource({ collection: NO_RUN_RECORDED }))).toBe(false);
    expect(needsAttention(wafSource({ collection: NO_RUN_RECORDED }))).toBe(true);
  });

  it.each([
    "failed",
    "not_eligible",
    "source_busy",
    "capacity_unavailable",
    "history_reading",
    "budget_exhausted",
    "no_progress",
    "no_run_recorded",
  ] as const)("does not judge a source in state %s", (state) => {
    expect(isJudged(wafSource({ collection: wafCollection({ state }) }))).toBe(false);
  });

  it.each(["collecting", "no_interval_due"] as const)("judges a source in state %s", (state) => {
    expect(isJudged(wafSource({ collection: wafCollection({ state }) }))).toBe(true);
  });

  it("does not judge a collecting source whose profile is disabled or not current", () => {
    expect(isJudged(wafSource({ enabled: false }))).toBe(false);
    expect(isJudged(wafSource({ validation_current: false }))).toBe(false);
  });

  it("tones only collecting as ok", () => {
    expect(collectionTone("collecting")).toBe("ok");
    expect(collectionTone("failed")).toBe("crit");
    expect(collectionTone("no_run_recorded")).toBe("warn");
    expect(collectionTone("history_reading")).toBe("");
  });
});
