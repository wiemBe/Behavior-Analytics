import { describe, expect, it } from "vitest";

import { wafListItem } from "./fixtures";
import {
  WAF_TYPES,
  anomalyTypeLabel,
  evidenceLabel,
  isWafRow,
  isWafType,
  stateLabel,
  subjectText,
} from "./wafLabels";

describe("anomalyTypeLabel", () => {
  it("never shows a raw enum string for a WAF type that has a name", () => {
    for (const type of WAF_TYPES) {
      const label = anomalyTypeLabel(type);
      expect(label).not.toContain("WAF_");
      expect(label).not.toBe(type);
    }
    expect(anomalyTypeLabel("WAF_REQUEST_RATE_SPIKE")).toBe("Request-rate spike");
  });

  it("falls through to the Phase A names, and shows an unknown type as sent", () => {
    expect(anomalyTypeLabel("VOLUME_SPIKE")).toBe("Volume spike");
    expect(anomalyTypeLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });

  it("knows which types are WAF types", () => {
    expect(isWafType("WAF_BLOCK_RATIO_SPIKE")).toBe(true);
    expect(isWafType("VOLUME_SPIKE")).toBe(false);
    expect(isWafType(undefined)).toBe(false);
  });
});

describe("subjectText", () => {
  it("renders a WAF subject as method, path and source", () => {
    expect(subjectText(wafListItem())).toBe("GET /portal/login · lab-waf-behavior-01");
  });

  it("renders a Phase A subject as its source name, falling back to the id", () => {
    const phaseA = wafListItem({ subject_kind: "LOG_SOURCE", waf_endpoint: null, log_source_name: "LAB Firewall" });
    expect(subjectText(phaseA)).toBe("LAB Firewall");
    expect(subjectText({ ...phaseA, log_source_name: null })).toBe(phaseA.log_source_id);
  });

  it("does not treat a WAF row with no endpoint as a WAF subject", () => {
    expect(isWafRow(wafListItem({ waf_endpoint: null }))).toBe(false);
  });
});

describe("stateLabel and evidenceLabel", () => {
  it("speak a lifecycle state and an evidence status as words", () => {
    expect(stateLabel("INSUFFICIENT_DATA")).toBe("Insufficient data");
    expect(stateLabel("NORMAL")).toBe("Normal");
    expect(stateLabel("RESOLVED")).toBe("Resolved");
    expect(evidenceLabel("NOT_REQUESTED")).toBe("Not requested");
    expect(evidenceLabel("PARTIAL")).toBe("Partial");
    expect(evidenceLabel("COMPLETE")).toBe("Complete");
    expect(evidenceLabel("UNAVAILABLE")).toBe("Unavailable");
  });

  it("shows an unknown value as sent", () => {
    expect(stateLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(evidenceLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
});
