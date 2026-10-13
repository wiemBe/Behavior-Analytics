import { describe, expect, it } from "vitest";

import { parseSubjectQuery } from "./subjectQuery";

describe("parseSubjectQuery", () => {
  it("accepts the two subject kinds and nothing else", () => {
    expect(parseSubjectQuery({ subject_kind: "WAF_ENDPOINT" }).subject_kind).toBe("WAF_ENDPOINT");
    expect(parseSubjectQuery({ subject_kind: "LOG_SOURCE" }).subject_kind).toBe("LOG_SOURCE");
    expect(parseSubjectQuery({ subject_kind: "<script>" }).subject_kind).toBe("");
    expect(parseSubjectQuery({}).subject_kind).toBe("");
  });

  it("accepts a WAF detector type from anomaly_type and drops anything else", () => {
    expect(parseSubjectQuery({ anomaly_type: "WAF_BLOCK_RATIO_SPIKE" }).waf_anomaly_type).toBe(
      "WAF_BLOCK_RATIO_SPIKE",
    );
    // A Phase A type is `parseAnomalyQuery`'s to parse, not this function's.
    expect(parseSubjectQuery({ anomaly_type: "VOLUME_SPIKE" }).waf_anomaly_type).toBe("");
    expect(parseSubjectQuery({ anomaly_type: "WAF_NOPE" }).waf_anomaly_type).toBe("");
  });
});
