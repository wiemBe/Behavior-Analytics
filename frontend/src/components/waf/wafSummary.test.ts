import { describe, expect, it } from "vitest";

import { wafDetail, wafDetection } from "./fixtures";
import { summarizeWafAnomaly } from "./wafSummary";

describe("summarizeWafAnomaly", () => {
  it("states the request volume against the expected volume, with the unit", () => {
    const s = summarizeWafAnomaly(wafDetail(), 300);
    expect(s.text).toBe(
      "Requests to GET /portal/login reached 412 requests / 5 min, against an expected 41.5 requests / 5 min.",
    );
    expect(s.caveat).toBeNull();
  });

  it("says 'at least' and carries a caveat when the count is a lower bound", () => {
    const s = summarizeWafAnomaly(
      wafDetail({ detection: wafDetection({ request_count_measurement: "LOWER_BOUND" }) }),
      300,
    );
    expect(s.text).toContain("reached ≥ 412 requests");
    expect(s.caveat).toMatch(/lower bound/);
  });

  it("states a proportion detector as a share, with the counts it was computed from", () => {
    const s = summarizeWafAnomaly(
      wafDetail({
        anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
        observed_value: 0.318,
        expected_value: 0.021,
        detection: wafDetection({
          observed_proportion: 0.318,
          expected_proportion: 0.021,
          observed_numerator: 318,
          observed_denominator: 1000,
        }),
      }),
      300,
    );
    expect(s.text).toBe(
      "The blocked share of requests for GET /portal/login was 31.8\u00A0% (318 of 1,000 requests), against an expected 2.1\u00A0%.",
    );
  });

  it("does not render a ratio detector's proportion as a request count", () => {
    const s = summarizeWafAnomaly(
      wafDetail({ anomaly_type: "WAF_ERROR_RATIO_SPIKE", observed_value: 0.4, expected_value: 0.01, detection: null }),
      300,
    );
    expect(s.text).toContain("40.0\u00A0%");
    expect(s.text).not.toContain("requests / 5 min");
  });

  // T-090: was "states a new endpoint as absent from the verified history" and pinned
  // "received 87 requests / 5 min". The count is cumulative since the endpoint first appeared,
  // not a per-bucket rate, so the per-bucket unit misstated what was measured.
  it("states a new endpoint's cumulative count since it first appeared, with none expected", () => {
    const s = summarizeWafAnomaly(
      wafDetail({ anomaly_type: "WAF_NEW_ENDPOINT_ACCESS", observed_value: 87, expected_value: 0, detection: null }),
      300,
    );
    expect(s.text).toContain("received 87 requests since it first appeared");
    expect(s.text).toContain("none were expected");
    expect(s.text).not.toContain("/ 5 min");
  });

  it("says 'per bucket' when the bucket width is not known, rather than guessing 5 min", () => {
    expect(summarizeWafAnomaly(wafDetail(), null).text).toContain("requests / bucket");
  });

  it("falls back to the server's own sentence for a type it has no phrasing for", () => {
    const s = summarizeWafAnomaly(
      wafDetail({ anomaly_type: "WAF_CLIENT_ENDPOINT_FANOUT_SPIKE", explanation: "the server says so" }),
      300,
    );
    expect(s.text).toBe("the server says so");
  });

  it("uses no causal language", () => {
    const text = summarizeWafAnomaly(wafDetail(), 300).text.toLowerCase();
    for (const word of ["caused by", "root cause", "attacker", "compromise", "malicious", "because of"]) {
      expect(text).not.toContain(word);
    }
  });
});
