import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { SUMMARY, classSummary, row, sourceRow, summary, GONE } from "./fixtures";
import {
  classImpact,
  classLines,
  defaultGraceText,
  defaultMode,
  formatGrace,
  graceParts,
  isGone,
  overrideSplit,
  parseGrace,
  readError,
  removeFailure,
  saveFailure,
  sourceImpact,
  type ClassChange,
  type SourceChange,
} from "./outage";

const BASE: ClassChange = {
  source_class: "FIREWALL",
  fromMode: "ALERT",
  fromGrace: null,
  toMode: "RECORD_ONLY",
  toGrace: null,
  covered: 12,
  defaultGrace: "1 bucket (5 min)",
  alertingOverrides: 2,
  recordOnlyOverrides: 0,
  removal: false,
  bucketSeconds: 300,
};

describe("classImpact", () => {
  it("counts the sources that stop alerting and those that keep alerting", () => {
    const { lines, known } = classImpact(BASE);
    expect(known).toBe(true);
    expect(lines[0]).toBe("12 monitored sources in Firewall will no longer raise outage alerts.");
    expect(lines[1]).toBe("2 sources keep alerting through their own overrides.");
  });

  it("says the opposite when switching back to ALERT", () => {
    const { lines } = classImpact({
      ...BASE,
      fromMode: "RECORD_ONLY",
      toMode: "ALERT",
      alertingOverrides: 0,
      recordOnlyOverrides: 1,
    });
    expect(lines[0]).toBe("12 monitored sources in Firewall will raise outage alerts again.");
    expect(lines[1]).toBe("1 source stays record-only through its own override.");
  });

  it("states no kept sources when there are no overrides", () => {
    const { lines } = classImpact({ ...BASE, alertingOverrides: 0 });
    expect(lines).toHaveLength(1);
  });

  it("always counts monitored sources", () => {
    const { lines } = classImpact({ ...BASE, covered: 3, alertingOverrides: 0 });
    expect(lines[0]).toBe("3 monitored sources in Firewall will no longer raise outage alerts.");
    expect(lines.join(" ")).not.toMatch(/enabled/);
  });

  it("does not claim a count it does not have", () => {
    const { lines, known } = classImpact({ ...BASE, covered: null });
    expect(known).toBe(false);
    expect(lines[0]).toMatch(/unavailable/);
    expect(lines.join(" ")).not.toMatch(/\b0\b/);
  });

  it("says when the row decides no source", () => {
    const { lines } = classImpact({ ...BASE, covered: 0 });
    expect(lines[0]).toMatch(/no source changes behaviour/i);
  });

  it("reports a grace-only change in buckets and minutes", () => {
    const { lines } = classImpact({
      ...BASE,
      fromMode: "RECORD_ONLY",
      toMode: "RECORD_ONLY",
      toGrace: 12,
    });
    expect(lines[0]).toBe("The mode stays Record only.");
    expect(lines[1]).toBe(
      "A silent source is judged after 12 buckets · 60 min instead of the default grace, 1 bucket (5 min).",
    );
  });

  it("describes clearing the grace", () => {
    const { lines } = classImpact({
      ...BASE,
      fromMode: "ALERT",
      toMode: "ALERT",
      fromGrace: 2,
      toGrace: null,
    });
    expect(lines[1]).toMatch(
      /the default grace, 1 bucket \(5 min\) instead of 2 buckets · 10 min/,
    );
  });

  it("falls back to buckets alone when the bucket width is unknown", () => {
    const { lines } = classImpact({
      ...BASE,
      fromMode: "ALERT",
      toMode: "ALERT",
      toGrace: 6,
      bucketSeconds: null,
      defaultGrace: "1 bucket",
    });
    expect(lines.join(" ")).toMatch(/6 buckets/);
    expect(lines.join(" ")).not.toMatch(/min/);
  });

  it("removing a row names the fallback and changes no mode when it is the same", () => {
    const { lines } = classImpact({
      ...BASE,
      fromMode: "ALERT",
      toMode: "ALERT",
      removal: true,
    });
    expect(lines[0]).toBe("Firewall already resolves to Alert; removing the row changes no mode.");
  });

  it("cannot count when the default mode is not reported", () => {
    const { known, lines } = classImpact({ ...BASE, toMode: null, removal: true });
    expect(known).toBe(false);
    expect(lines[0]).toMatch(/default mode is not reported/);
  });
});

const SRC: SourceChange = {
  name: "PaSeries @ pa-fw-02",
  source_class: "FIREWALL",
  classMode: "RECORD_ONLY",
  fromMode: "RECORD_ONLY",
  fromGrace: null,
  toMode: "ALERT",
  toGrace: null,
  removal: false,
  bucketSeconds: 300,
  defaultGrace: "1 bucket (5 min)",
};

describe("sourceImpact", () => {
  it("says the source alerts although its class does not", () => {
    const { lines } = sourceImpact(SRC);
    expect(lines[0]).toBe("PaSeries @ pa-fw-02 will raise outage alerts.");
    expect(lines[1]).toMatch(/Firewall resolves to Record only; this source differs/);
  });

  it("says the source stops alerting", () => {
    const { lines } = sourceImpact({ ...SRC, classMode: "ALERT", fromMode: "ALERT", toMode: "RECORD_ONLY" });
    expect(lines[0]).toBe("PaSeries @ pa-fw-02 will no longer raise outage alerts.");
  });

  it("says a same-as-class override only pins the source", () => {
    const { lines } = sourceImpact({ ...SRC, toMode: "RECORD_ONLY" });
    expect(lines.join(" ")).toMatch(/pins the source/);
  });

  it("describes removal as following the class", () => {
    const { lines } = sourceImpact({ ...SRC, fromMode: "ALERT", removal: true });
    expect(lines[0]).toBe("PaSeries @ pa-fw-02 will no longer raise outage alerts.");
    expect(lines).toContain("It follows Firewall again.");
  });

  it("does not state an effect when the class mode is unknown", () => {
    const { known } = sourceImpact({ ...SRC, classMode: null, removal: true });
    expect(known).toBe(false);
  });
});

describe("grace", () => {
  it("shows buckets and minutes from the API's bucket width", () => {
    expect(formatGrace(6, 300)).toBe("6 buckets · 30 min");
    expect(formatGrace(1, 300)).toBe("1 bucket · 5 min");
    // Not a hard-coded 5 minutes.
    expect(formatGrace(6, 60)).toBe("6 buckets · 6 min");
    expect(graceParts(3, 300)).toEqual({ buckets: "3 buckets", minutes: "15 min" });
  });

  it("shows buckets alone when the width is unknown", () => {
    expect(formatGrace(6, null)).toBe("6 buckets");
    expect(graceParts(6, null).minutes).toBeNull();
  });

  it("parses blank as the default and rejects what is not a whole number", () => {
    expect(parseGrace("  ")).toEqual({ ok: true, value: null });
    expect(parseGrace("0")).toEqual({ ok: true, value: 0 });
    expect(parseGrace("99")).toEqual({ ok: true, value: 99 });
    expect(parseGrace("2.5").ok).toBe(false);
    expect(parseGrace("abc").ok).toBe(false);
  });
});

describe("classLines", () => {
  it("lists a class with an enabled source, and one that has a row, and nothing else", () => {
    const lines = classLines(SUMMARY, [row({ scope_value: "DNS" })]);
    expect(lines.map((l) => l.source_class)).toEqual(["DNS", "FIREWALL", "LINUX", "PROXY"]);
  });

  it("takes the mode from the default when there is no row, and from the row when there is", () => {
    const lines = classLines(SUMMARY, [row({ mode: "RECORD_ONLY", silence_grace_buckets: 6 })]);
    const fw = lines.find((l) => l.source_class === "FIREWALL");
    const linux = lines.find((l) => l.source_class === "LINUX");
    expect(fw?.row).toBeDefined();
    expect(fw?.grace).toBe(6);
    expect(fw?.covered).toBe(12);
    expect(linux?.row).toBeUndefined();
    expect(linux?.mode).toBe("ALERT");
    // No row: the monitored count, not the enabled one.
    expect(linux?.covered).toBe(8);
  });

  it("subtracts source overrides from a default class's monitored count", () => {
    const s = summary([classSummary("LINUX", 8, { outage_overrides: 3, monitored_sources: 6 })]);
    expect(classLines(s, [])[0].covered).toBe(3);
  });

  it("keeps an instance-scoped class row on its own line", () => {
    const lines = classLines(SUMMARY, [row({ scope_value: "LINUX", instance_id: "7c1e0000-0000" })]);
    const linux = lines.filter((l) => l.source_class === "LINUX");
    expect(linux).toHaveLength(2);
    expect(linux[0].row).toBeUndefined();
    expect(linux[1].row?.instance_id).toBe("7c1e0000-0000");
    expect(linux[1].covered).toBeNull();
  });

  it("does not invent counts when the summary is missing", () => {
    const lines = classLines(null, [row()]);
    expect(lines).toHaveLength(1);
    expect(lines[0].covered).toBe(12);
    expect(lines[0].overrides).toBeNull();
  });
});

describe("rows", () => {
  it("splits source overrides by what they do, ignoring removed sources", () => {
    const rows = [
      sourceRow(),
      sourceRow({ scope_value: "b", mode: "RECORD_ONLY", source_name: "x" }),
      sourceRow({ scope_value: GONE, mode: "ALERT", source_name: null, source_class: null }),
    ];
    expect(overrideSplit(rows, "FIREWALL")).toEqual({ alerting: 1, recordOnly: 1 });
    expect(isGone(rows[2])).toBe(true);
    expect(isGone(rows[0])).toBe(false);
  });

  it("reads the default mode from the summary, even when every class has a row", () => {
    expect(defaultMode(SUMMARY)).toBe("ALERT");
    expect(defaultMode(null)).toBeNull();
    const all = summary([classSummary("FIREWALL", 1)], 300, { outage_default: "RECORD_ONLY" });
    expect(defaultMode(all)).toBe("RECORD_ONLY");
  });

  it("states the default grace in buckets and minutes", () => {
    expect(defaultGraceText(SUMMARY)).toBe("1 bucket (5 min)");
    expect(defaultGraceText(summary([], 60, { default_silence_grace_buckets: 3 }))).toBe(
      "3 buckets (3 min)",
    );
    expect(defaultGraceText(summary([], 300, { default_silence_grace_buckets: null }))).toBeNull();
    expect(defaultGraceText(null)).toBeNull();
  });
});

describe("failures", () => {
  it("names the permission on a 403", () => {
    expect(saveFailure(new ApiError(403, "no")).text).toMatch(/admin:outage/);
    expect(readError(new ApiError(403, "no")).forbidden).toBe(true);
    expect(readError(new ApiError(403, "no")).text).toMatch(/admin:outage/);
  });

  it("puts a 422 at the grace field", () => {
    const f = saveFailure(new ApiError(422, "Input should be less than or equal to 48"));
    expect(f.field).toBe("grace");
    expect(f.text).toMatch(/48/);
  });

  it("treats a 404 on remove as already removed", () => {
    const f = removeFailure(new ApiError(404, "outage policy row not found"));
    expect(f.gone).toBe(true);
    expect(f.text).toMatch(/already removed/);
    expect(removeFailure(new ApiError(500, "x")).gone).toBe(false);
  });

  it("never prints a raw error", () => {
    const f = readError(new Error("connect ECONNREFUSED http://internal:8000"));
    expect(f.forbidden).toBe(false);
    expect(f.text).not.toMatch(/ECONNREFUSED|internal/);
  });
});
