import { describe, expect, it } from "vitest";

import { ApiError, type FleetClassSummary, type FleetSource } from "@/lib/api";
import {
  completenessInfo,
  conflictText,
  expectedInfo,
  formatEpsValue,
  formatEventCount,
  openIncidentList,
  originLabel,
  parseFleetSourceQuery,
  parseWindow,
  ruleFailure,
  sourceFailure,
} from "./fleet";

function cls(over: Partial<FleetClassSummary> = {}): FleetClassSummary {
  return {
    source_class: "WAF",
    sources: { enabled: 4, disabled: 0 },
    buckets: { expected: 12, complete: 12, partial: 0, missing: 0 },
    events: 100,
    mean_eps: 1,
    expected_eps: 2,
    baseline_sources: 4,
    baseline_share: 1,
    monitored_sources: 4,
    open_incidents: {},
    top_sources: [],
    ...over,
  };
}

describe("null is not zero", () => {
  it("keeps a measured zero apart from a missing value", () => {
    expect(formatEpsValue(0)).toBe("0.00");
    expect(formatEpsValue(null)).toBeNull();
    expect(formatEventCount(0)).toBe("0");
    expect(formatEventCount(null)).toBeNull();
  });
});

describe("completenessInfo", () => {
  it("is complete only when every expected bucket is complete", () => {
    expect(completenessInfo({ expected: 12, complete: 12, partial: 0, missing: 0 }).state).toBe(
      "complete",
    );
  });
  it("is incomplete with partial and missing counts", () => {
    const i = completenessInfo({ expected: 12, complete: 8, partial: 3, missing: 1 });
    expect(i.state).toBe("incomplete");
    expect(i.detail).toBe("3 partial, 1 missing");
  });
  it("says there are no monitored sources rather than 100% complete", () => {
    const i = completenessInfo({ expected: 0, complete: 0, partial: 0, missing: 0 });
    expect(i.state).toBe("none");
  });
});

describe("expectedInfo", () => {
  it("is null with its meaning when no source has a baseline", () => {
    const e = expectedInfo(cls({ expected_eps: null, baseline_sources: 0, baseline_share: 0 }));
    expect(e.value).toBeNull();
    expect(e.note).toMatch(/no source has a baseline/i);
  });
  it("states N of M sources and flags a partial basis", () => {
    const e = expectedInfo(cls({ baseline_sources: 3, baseline_share: 0.5 }));
    expect(e.note).toContain("3 of 6 sources");
    expect(e.partial).toBe(true);
  });
  it("is not partial when every source has a baseline", () => {
    expect(expectedInfo(cls()).partial).toBe(false);
  });
});

describe("openIncidentList", () => {
  it("names detectors and drops zero counts", () => {
    expect(openIncidentList(cls({ open_incidents: { VOLUME_DROP: 2, NO_EVENTS: 0 } }))).toEqual([
      ["Volume drop", 2],
    ]);
  });
});

describe("conflictText", () => {
  const base = { basis: "TYPE_NAME_REGEX", source_class: "IPS" as const };
  it("reads type says IPS, name says Linux", () => {
    const text = conflictText({
      ...base,
      conflicts: [{ basis: "NAME_REGEX", rule_id: null, class: "LINUX" }],
    } as Pick<FleetSource, "basis" | "source_class" | "conflicts">);
    expect(text).toBe("type says IPS, name says Linux");
  });
  it("is empty without conflicts", () => {
    expect(conflictText({ ...base, conflicts: [] })).toBe("");
  });
});

describe("originLabel", () => {
  it("does not turn unknown into no", () => {
    expect(originLabel(null)).toMatch(/not recorded/i);
    expect(originLabel(true)).toBe("Auto-discovered");
    expect(originLabel(false)).toBe("Configured");
  });
});

describe("query parsing", () => {
  it("allow-lists every value", () => {
    const q = parseFleetSourceQuery({ class: "NOPE", review_status: "NEW", has_conflict: "x", offset: "-3" });
    expect(q.class).toBe("");
    expect(q.review_status).toBe("NEW");
    expect(q.has_conflict).toBe("");
    expect(q.offset).toBe(0);
  });
  it("defaults the window to 24h", () => {
    expect(parseWindow("bogus")).toBe("24h");
    expect(parseWindow("7d")).toBe("7d");
  });
});

describe("failures", () => {
  it("names the permission on a 403", () => {
    expect(ruleFailure(new ApiError(403, "x")).kind).toBe("forbidden");
    expect(sourceFailure(new ApiError(403, "x")).text).toContain("log_source:write");
  });
  it("shows a 422 verbatim and attributes a pattern message to the pattern field", () => {
    const f = ruleFailure(new ApiError(422, "pattern is not a valid regular expression: boom"));
    expect(f.field).toBe("pattern");
    expect(f.text).toContain("boom");
  });
  it("shows a 409 duplicate at the pattern", () => {
    expect(ruleFailure(new ApiError(409, "a rule with this instance, kind and pattern already exists")).field).toBe("pattern");
  });
});
