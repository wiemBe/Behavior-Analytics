import { describe, expect, it } from "vitest";

import { bucket, catalog, value, watch } from "./fixtures";
import {
  baselineText,
  coverageText,
  eventsUnit,
  expectedRangeText,
  formatEvents,
  needsAttention,
  readingText,
  relativeAge,
  scopeText,
  shareOfParsed,
  sortWatches,
  statusLabel,
  statusReason,
  statusTone,
} from "./pwFormat";
import { parsePwRange, parsePwSelection, defaultSelection } from "./pwRange";

describe("statuses", () => {
  it("gives every status a word and a reason", () => {
    const all = [
      "NOT_ENABLED", "DISABLED", "SCOPE_EMPTY", "SCOPE_STALE", "SCOPE_TOO_LARGE",
      "NOT_APPLICABLE", "STALE", "OPEN", "RECOVERING", "OK",
    ] as const;
    const labels = all.map((s) => statusLabel(s));
    expect(new Set(labels).size).toBe(all.length);
    for (const s of all) expect(statusReason(s, null).length).toBeGreaterThan(10);
  });

  it("keeps the API's reason over the fixed meaning", () => {
    expect(statusReason("SCOPE_STALE", "inventory synced 9 h ago")).toBe("inventory synced 9 h ago");
  });

  it("does not call a watch that never ran healthy", () => {
    expect(statusLabel(null)).toBe("Never run");
    expect(statusTone(null)).toBe("");
    expect(statusReason(null, null)).toMatch(/not judged/);
  });

  it("takes the positive tone only for OK", () => {
    for (const s of ["NOT_ENABLED", "DISABLED", "STALE", "OPEN", "NOT_APPLICABLE", "SCOPE_STALE"] as const) {
      expect(statusTone(s)).not.toBe("ok");
    }
    expect(statusTone("OK")).toBe("ok");
  });

  it("sorts the watches that need attention first, then by name", () => {
    const rows = [
      watch({ id: "1", name: "B ok", status: "OK" }),
      watch({ id: "2", name: "A disabled", status: "DISABLED" }),
      watch({ id: "3", name: "Z stale scope", status: "SCOPE_STALE" }),
      watch({ id: "4", name: "Y open", status: "OPEN" }),
      watch({ id: "5", name: "X never", status: null }),
    ];
    expect(sortWatches(rows).map((w) => w.name)).toEqual([
      "Y open", "Z stale scope", "A disabled", "B ok", "X never",
    ]);
    expect(needsAttention("OPEN")).toBe(true);
    expect(needsAttention("RECOVERING")).toBe(false);
    expect(needsAttention(null)).toBe(false);
  });
});

describe("numbers", () => {
  it("never formats a missing count as zero", () => {
    expect(formatEvents(null)).toBe("—");
    expect(formatEvents(undefined)).toBe("—");
    expect(formatEvents(0)).toBe("0");
  });

  it("reads a lower bound as at least n", () => {
    expect(formatEvents(1200, "LOWER_BOUND")).toBe("at least 1,200");
    expect(formatEvents(1200, "EXACT")).toBe("1,200");
  });

  it("says what a reading is when it is not a number", () => {
    expect(readingText("MEASURED_ZERO", 0, "EXACT")).toBe("0");
    expect(readingText("UNKNOWN", null, null)).toBe("unknown");
    expect(readingText("NOT_AN_OBSERVATION", null, null)).toBe("not tracked then");
    expect(readingText("NO_BUCKET", null, null)).toBe("not collected");
    expect(readingText("COUNT", 7, null)).toBe("—");
    expect(readingText("MEASURED_ZERO", 4, "EXACT")).toBe("—");
  });

  it("speaks the unit from the bucket width", () => {
    expect(eventsUnit(300)).toBe("events per 5 min");
    expect(eventsUnit(null)).toBe("events per bucket");
  });

  it("states coverage with 'at least' on a lower bound and a dash when unknown", () => {
    expect(coverageText({ bucket_start: "x", total_events: 1, parsed_events: 1, ratio: 0.96, measurement: "EXACT" })).toBe("96 %");
    expect(coverageText({ bucket_start: "x", total_events: 1, parsed_events: 1, ratio: 0.88, measurement: "LOWER_BOUND" })).toBe("at least 88 %");
    expect(coverageText({ bucket_start: "x", total_events: null, parsed_events: null, ratio: null, measurement: "EXACT" })).toBe("—");
    expect(coverageText(null)).toBe("—");
  });

  it("names the scope even when the inventory no longer lists it", () => {
    expect(scopeText(watch())).toBe("Log source group: DMZ firewalls");
    const gone = watch({ scope: { kind: "LOG_SOURCE_TYPE", name: null, detail: null, qradar_id: 42, member_count: null } });
    expect(scopeText(gone)).toBe("Log source type 42: not in the synced inventory");
  });

  it("gives a relative age that never replaces the time and never goes negative", () => {
    const now = new Date("2026-10-18T09:00:00Z");
    expect(relativeAge("2026-10-18T08:48:00Z", now)).toBe("12 min ago");
    expect(relativeAge("2026-10-18T09:30:00Z", now)).toBe("—");
    expect(relativeAge(null, now)).toBe("—");
  });
});

describe("a value's table cells", () => {
  it("shows a share only for an exact value in an exact bucket with a whole total", () => {
    const b = bucket(11);
    expect(shareOfParsed(value("a", "alice", { events: 96 }), b)).toBeCloseTo(0.1);
    expect(shareOfParsed(value("a", "alice", { events: 96, measurement: "LOWER_BOUND" }), b)).toBeNull();
    expect(shareOfParsed(value("a", "alice", { events: 96 }), bucket(11, { values_measurement: "LOWER_BOUND" }))).toBeNull();
    expect(shareOfParsed(value("a", "alice", { events: 96 }), bucket(11, { totals_complete: false }))).toBeNull();
    expect(shareOfParsed(value("a", "alice", { events: 96 }), bucket(11, { parsed_events: 0 }))).toBeNull();
    expect(shareOfParsed(value("a", "alice", { reading: "UNKNOWN", events: null, measurement: null }), b)).toBeNull();
    expect(shareOfParsed(value("a", "alice", { latest_bucket_start: "2026-10-01T00:00:00Z" }), b)).toBeNull();
    expect(shareOfParsed(value("a", "alice"), null)).toBeNull();
  });

  it("states the expected range, or why there is none", () => {
    expect(expectedRangeText(value("a", "alice"))).toBe("80 – 140");
    expect(expectedRangeText(value("a", "alice", { baseline_state: "LEARNING" }))).toBe("learning");
    expect(expectedRangeText(value("a", "alice", { baseline_state: "NONE" }))).toBe("no baseline");
    expect(expectedRangeText(value("a", "alice", { expected_low: null }))).toBe("no range for this hour");
  });

  it("states learning as n of 168 cells", () => {
    expect(baselineText(value("a", "alice", { baseline_state: "LEARNING", reliable_cells: 31 }))).toBe(
      "Learning (31 of 168 cells)",
    );
    expect(baselineText(value("a", "alice"))).toBe("Baseline reliable (168 of 168 cells)");
    expect(baselineText(value("a", "alice", { baseline_state: "NONE", reliable_cells: 0 }))).toBe("No baseline yet");
  });
});

describe("the view parameters", () => {
  it("falls back to 24 hours and offers exactly four windows", () => {
    expect(parsePwRange("6h").hours).toBe(6);
    expect(parsePwRange("7d").hours).toBe(168);
    expect(parsePwRange("999h").hours).toBe(24);
    expect(parsePwRange(undefined).hours).toBe(24);
  });

  it("accepts value ids only, at most five, never a value's text", () => {
    const id = (n: number) => `6f1c0f9e-0000-4000-8000-00000000000${n}`;
    expect(parsePwSelection([id(1), "alice", id(1), id(2)])).toEqual([id(1), id(2)]);
    expect(parsePwSelection([1, 2, 3, 4, 5, 6, 7].map((n) => id(n)))).toHaveLength(5);
    expect(parsePwSelection(undefined)).toEqual([]);
  });

  it("selects the pinned values, then the top values of the latest bucket, at most five", () => {
    const items = [
      value("p1", "pin", { pinned: true, events: null, reading: "UNKNOWN", measurement: null }),
      value("a", "a", { events: 5 }),
      value("b", "b", { events: 90 }),
      value("z", "z", { events: 0, reading: "MEASURED_ZERO" }),
      value("c", "c", { events: 40 }),
      value("d", "d", { events: 30 }),
      value("e", "e", { events: 20 }),
    ];
    expect(defaultSelection(items, [])).toEqual(["p1", "b", "c", "d", "e"]);
    expect(defaultSelection(items, ["a"])).toEqual(["a"]);
  });
});

describe("the catalog fixture", () => {
  it("carries the coverage floor as a fraction", () => {
    expect(catalog().min_coverage).toBe(0.9);
  });
});
