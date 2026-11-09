import { describe, expect, it } from "vitest";

import { ENGLISH_ANOMALIES } from "@/lib/anomaliesI18n";

import { formatMatches, formatTimes, isRuleRow, isRuleType, ruleTypeLabel, windowWidthText } from "./ruleMatchFormat";


describe("rule-match formatting", () => {
  it("shows a missing count as the dash and a measured zero as 0", () => {
    expect(formatMatches(null)).toBe("—");
    expect(formatMatches(undefined)).toBe("—");
    expect(formatMatches(Number.NaN)).toBe("—");
    expect(formatMatches(0)).toBe("0");
    expect(formatMatches(4800)).toBe("4,800");
    expect(formatMatches(310.5)).toBe("310.5");
  });

  it("gives a ratio its unit and a missing ratio none", () => {
    expect(formatTimes(15.456)).toBe("15.46 ×");
    expect(formatTimes(null)).toBe("—");
  });

  it("names the window width", () => {
    expect(windowWidthText(3600)).toBe("1 h");
    expect(windowWidthText(300)).toBe("5 min");
    expect(windowWidthText(86400)).toBe("1 d");
    expect(windowWidthText(90)).toBe("90 s");
  });

  it("knows the two rule detectors and labels them as words", () => {
    expect(isRuleType("RULE_SILENCE")).toBe(true);
    expect(isRuleType("RULE_STORM")).toBe(true);
    expect(isRuleType("VOLUME_SPIKE")).toBe(false);
    expect(ruleTypeLabel("RULE_STORM", ENGLISH_ANOMALIES)).toBe("Rule storm");
    expect(ruleTypeLabel("RULE_SILENCE", ENGLISH_ANOMALIES)).toBe("Rule silence");
    expect(ruleTypeLabel("RULE_FUTURE", ENGLISH_ANOMALIES)).toBe("RULE_FUTURE");
  });

  it("recognises a rule row only with its rule block", () => {
    expect(isRuleRow({ subject_kind: "RULE", rule: { rule_id: "r", name: "n", qradar_id: 1, is_building_block: false } })).toBe(true);
    expect(isRuleRow({ subject_kind: "RULE", rule: null })).toBe(false);
    expect(isRuleRow({ subject_kind: "LOG_SOURCE" })).toBe(false);
  });
});
