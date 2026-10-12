import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import {
  SCHEDULE_PRESETS,
  cadenceLabel,
  changeFailure,
  cronToPreset,
  familyInfo,
  formatBucket,
  formatDuration,
  outcomeInfo,
  presetToCron,
  scheduleLabel,
  worstCaseDelay,
} from "./searchCadence";

describe("formatDuration", () => {
  it("labels the seven presets as 5 min … 8 h", () => {
    expect([300, 900, 1800, 3600, 7200, 14400, 28800].map(formatDuration)).toEqual([
      "5 min",
      "15 min",
      "30 min",
      "1 h",
      "2 h",
      "4 h",
      "8 h",
    ]);
  });

  it("joins hours, minutes and seconds and drops zero parts", () => {
    expect(formatDuration(4020)).toBe("1 h 7 min");
    expect(formatDuration(90)).toBe("1 min 30 s");
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(3601)).toBe("1 h 1 s");
  });

  it("does not invent a duration from a bad number", () => {
    expect(formatDuration(-1)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });
});

describe("formatBucket", () => {
  it("shows a width, and an em dash for no buckets rather than 0", () => {
    expect(formatBucket(300)).toBe("5 min");
    expect(formatBucket(3600)).toBe("1 h");
    expect(formatBucket(null)).toBe("—");
  });
});

describe("worstCaseDelay", () => {
  it("is cadence + bucket + settle", () => {
    const d = worstCaseDelay(3600, 300, 120);
    expect(d.seconds).toBe(3600 + 300 + 120);
    expect(d.text).toBe("up to 1 h 7 min");
    expect(d.evidenceOnly).toBe(false);
  });

  it("bounds evidence arrival, not detection, when there is no bucket", () => {
    const d = worstCaseDelay(3600, null, 120);
    expect(d.seconds).toBe(3720);
    expect(d.text).toBe("evidence up to 1 h 2 min");
    expect(d.evidenceOnly).toBe(true);
  });

  it("counts a zero settle margin as zero, not as missing", () => {
    expect(worstCaseDelay(300, 300, 0).text).toBe("up to 10 min");
  });
});

describe("schedule presets", () => {
  it("pairs every preset with the §SC.6 cron", () => {
    expect(SCHEDULE_PRESETS.map((p) => [p.label, p.cron])).toEqual([
      ["5 min", "*/5 * * * *"],
      ["15 min", "*/15 * * * *"],
      ["30 min", "*/30 * * * *"],
      ["1 h", "0 * * * *"],
      ["2 h", "0 */2 * * *"],
      ["4 h", "0 */4 * * *"],
      ["8 h", "0 */8 * * *"],
    ]);
  });

  it("round-trips every preset between cron and seconds", () => {
    for (const p of SCHEDULE_PRESETS) {
      expect(cronToPreset(p.cron)?.seconds).toBe(p.seconds);
      expect(presetToCron(p.seconds)).toBe(p.cron);
    }
  });

  it("matches exactly: a near miss is Custom, never a preset", () => {
    expect(cronToPreset("0 * * * * ")).toBeNull();
    expect(cronToPreset("0 */3 * * *")).toBeNull();
    expect(cronToPreset("*/5 * * * 1-5")).toBeNull();
    expect(cronToPreset("")).toBeNull();
  });

  it("has no cron for a duration that is not a preset", () => {
    expect(presetToCron(600)).toBeNull();
  });

  it("labels a preset 'Every …' and anything else 'Custom'", () => {
    expect(scheduleLabel("0 * * * *")).toBe("Every 1 h");
    expect(scheduleLabel("17 3 * * 1")).toBe("Custom");
  });

  it("labels an allowed cadence the same way a bucket is labelled", () => {
    expect(cadenceLabel(28800)).toBe("8 h");
  });
});

describe("familyInfo", () => {
  it("names the four seeded families", () => {
    expect(familyInfo("RULE_MATCH").name).toBe("Rule matches");
    expect(familyInfo("ANOMALY_EXPLANATION").scope).toMatch(/evidence/i);
  });

  it("keeps an unknown family's key instead of hiding the row", () => {
    expect(familyInfo("NEW_FAMILY")).toEqual({ name: "NEW_FAMILY", scope: null });
  });
});

describe("changeFailure", () => {
  it("treats a 403 as a permission refusal that names the permission", () => {
    const f = changeFailure(new ApiError(403, "forbidden"), "Your role cannot do it (permission x:y).");
    expect(f.kind).toBe("forbidden");
    expect(f.text).toContain("permission x:y");
    expect(f.text).toMatch(/nothing was changed/i);
  });

  it("treats any other failure as an error that says nothing changed", () => {
    const f = changeFailure(new ApiError(500, "boom"), "unused");
    expect(f.kind).toBe("error");
    expect(f.text).toMatch(/^Not changed\./);
    expect(f.text).not.toContain("boom");
  });

  it("treats a network failure as an error, not a refusal", () => {
    expect(changeFailure(new TypeError("fetch failed"), "unused").kind).toBe("error");
  });
});

describe("outcomeInfo", () => {
  it("labels the outcomes the backend records", () => {
    expect(outcomeInfo("ok")).toEqual({ label: "Complete", tone: "ok" });
    expect(outcomeInfo("no-sources")?.label).toBe("No eligible WAF source");
    expect(outcomeInfo("dispatched")?.label).toBe("Dispatched");
    expect(outcomeInfo("partial")?.tone).toBe("warn");
    expect(outcomeInfo("failed")?.tone).toBe("crit");
    expect(outcomeInfo("no-instance")).toEqual({
      label: "No enabled instance",
      tone: "neutral",
    });
  });
  it("has no label for an unknown outcome, so it is shown verbatim", () => {
    expect(outcomeInfo("COMPLETE")).toBeNull();
    expect(outcomeInfo("")).toBeNull();
  });
});
