import { describe, expect, it } from "vitest";

import { admission, ipsSource, latestBucket, pressure } from "./behaviorFixtures";
import {
  activeAnomaliesHref,
  admissionNotMeasured,
  atCeiling,
  groupByMeasurement,
  hasActiveAnomalies,
  ipsValidationView,
  isJudged,
  isOverflowing,
  needsAttention,
  sortByAttention,
} from "./ipsSourceHealth";

describe("isJudged", () => {
  it("needs an enabled profile that is VALID for its current version", () => {
    expect(isJudged(ipsSource())).toBe(true);
    expect(isJudged(ipsSource({ enabled: false }))).toBe(false);
    expect(isJudged(ipsSource({ validation_current: false }))).toBe(false);
    expect(isJudged(ipsSource({ validation_status: "INVALID", validation_current: false }))).toBe(false);
  });
});

describe("ipsValidationView", () => {
  it("never reads a profile valid for an older version as valid", () => {
    const v = ipsValidationView(
      ipsSource({ profile_version: 5, validated_profile_version: 4, validation_current: false }),
    );
    expect(v.label).toBe("STALE");
    expect(v.tone).toBe("warn");
    expect(v.detail).toBe("VALID for v4; profile is now v5");
  });

  it("says an unvalidated profile was never validated", () => {
    const v = ipsValidationView(ipsSource({ validation_status: "UNVALIDATED", validation_current: false }));
    expect(v.label).toBe("UNVALIDATED");
    expect(v.detail).toBe("profile v3 was never validated");
  });

  it("marks invalid as critical and a status it does not know as the raw code", () => {
    expect(ipsValidationView(ipsSource({ validation_status: "INVALID", validation_current: false })).tone).toBe(
      "crit",
    );
    expect(ipsValidationView(ipsSource({ validation_status: "ODD", validation_current: false })).label).toBe("ODD");
  });
});

describe("overflow and ceiling", () => {
  it("is overflowing when entities were refused over the quota or admission was truncated", () => {
    const refused = latestBucket({
      admission: [admission("DESTINATION_PORT", { rejected_over_quota: 7 })],
    });
    expect(isOverflowing(ipsSource({ latest_bucket: refused }))).toBe(true);
    const truncated = latestBucket({
      admission: [admission("SIGNATURE", { admission_truncated: true })],
    });
    expect(isOverflowing(ipsSource({ latest_bucket: truncated }))).toBe(true);
  });

  it("does not count entities below the admission threshold, nor a source with no bucket", () => {
    const noise = latestBucket({
      admission: [admission("DESTINATION_PORT", { below_admission_threshold: 900 })],
    });
    expect(isOverflowing(ipsSource({ latest_bucket: noise }))).toBe(false);
    expect(isOverflowing(ipsSource({ latest_bucket: null }))).toBe(false);
  });

  it("tells not measured from none", () => {
    const blank = latestBucket({
      admission: [
        {
          dimension: "DESTINATION_PORT",
          admitted: null,
          below_admission_threshold: null,
          rejected_over_quota: null,
          untracked_entities: null,
          untracked_events: null,
          admission_truncated: null,
        },
      ],
    });
    expect(admissionNotMeasured(ipsSource({ latest_bucket: blank }))).toBe(true);
    expect(admissionNotMeasured(ipsSource())).toBe(false);
  });

  it("is at the ceiling when any dimension reaches it", () => {
    expect(atCeiling(ipsSource())).toBe(false);
    expect(atCeiling(ipsSource({ entities: [pressure("SIGNATURE", 500, 520)] }))).toBe(true);
  });
});

describe("sortByAttention", () => {
  it("lists a source that is not judged, overflowing or at the ceiling before a healthy one", () => {
    const healthy = ipsSource({ log_source_id: "a", log_source_name: "alpha-ok" });
    const disabled = ipsSource({ log_source_id: "b", log_source_name: "bravo-disabled", enabled: false });
    const full = ipsSource({
      log_source_id: "c",
      log_source_name: "charlie-full",
      entities: [pressure("DESTINATION_IP", 500, 500)],
    });
    expect(needsAttention(healthy)).toBe(false);
    expect(sortByAttention([healthy, full, disabled]).map((s) => s.log_source_name)).toEqual([
      "bravo-disabled",
      "charlie-full",
      "alpha-ok",
    ]);
  });
});

describe("groupByMeasurement", () => {
  it("groups the dimensions by their measurement in the API order", () => {
    const groups = groupByMeasurement(latestBucket().dimensions.map((d, i) => (i === 3 ? { ...d, measurement: "LOWER_BOUND" } : d)));
    expect(groups.map((g) => g.measurement)).toEqual(["EXACT", "LOWER_BOUND"]);
    expect(groups[1].dimensions).toEqual(["SOURCE_IP"]);
  });
});

describe("sortByAttention with active anomalies", () => {
  const named = (name: string, over: Parameters<typeof ipsSource>[0] = {}) =>
    ipsSource({ log_source_id: name, log_source_name: name, ...over });
  const order = (items: ReturnType<typeof ipsSource>[]) =>
    sortByAttention(items).map((s) => s.log_source_name);

  it("ranks a judged source with active anomalies above a judged one without", () => {
    expect(
      order([named("a-quiet"), named("b-busy", { active_anomaly_count: 1, worst_active_severity: "LOW" })]),
    ).toEqual(["b-busy", "a-quiet"]);
  });

  it("orders those by worst severity, then count, then name", () => {
    expect(
      order([
        named("d", { active_anomaly_count: 9, worst_active_severity: "MEDIUM" }),
        named("c", { active_anomaly_count: 1, worst_active_severity: "CRITICAL" }),
        named("b", { active_anomaly_count: 5, worst_active_severity: "HIGH" }),
        named("a", { active_anomaly_count: 2, worst_active_severity: "HIGH" }),
        named("e", { active_anomaly_count: 2, worst_active_severity: "HIGH" }),
      ]),
    ).toEqual(["c", "b", "a", "e", "d"]);
  });

  it("keeps a source that is not judged above every judged source, whatever their anomalies", () => {
    expect(
      order([
        named("busy", { active_anomaly_count: 50, worst_active_severity: "CRITICAL" }),
        named("not-judged", { active_anomaly_count: null, validation_current: false }),
      ]),
    ).toEqual(["not-judged", "busy"]);
  });

  it("puts a judged source with anomalies above a judged one that only overflows", () => {
    const overflowing = named("overflowing", {
      latest_bucket: latestBucket({ admission: [admission("SIGNATURE", { rejected_over_quota: 3 })] }),
    });
    expect(
      order([overflowing, named("busy", { active_anomaly_count: 1, worst_active_severity: "INFO" })]),
    ).toEqual(["busy", "overflowing"]);
  });
});

describe("active anomaly helpers", () => {
  it("counts null as not-having, never as an anomaly", () => {
    expect(hasActiveAnomalies(ipsSource({ active_anomaly_count: null }))).toBe(false);
    expect(hasActiveAnomalies(ipsSource({ active_anomaly_count: 0 }))).toBe(false);
    expect(hasActiveAnomalies(ipsSource({ active_anomaly_count: 2 }))).toBe(true);
  });

  it("links to the active IPS anomalies of one source", () => {
    expect(activeAnomaliesHref("abc")).toBe(
      "/anomalies?subject_kind=IPS&log_source_id=abc&active_only=true",
    );
  });
});
