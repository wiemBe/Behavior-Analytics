import { describe, expect, it } from "vitest";

import { FAMILY_INFO } from "@/lib/searchCadence";
import { ApiError } from "@/lib/api";

import {
  admissionSentences,
  barValue,
  buildSlots,
  collectedUpTo,
  emptyKind,
  breakParts,
  formatCount,
  formatSeen,
  gapBand,
  measurementText,
  niceMax,
  formatEvents24h,
  gapRuns,
  senderFailure,
  stepMeasured,
} from "./catchall";
import { bucket } from "./fixtures";

const T = (m: number) => Date.UTC(2026, 9, 13, 10, m);

describe("buildSlots", () => {
  it("marks a bucket with no row inside the range and before the latest measured bucket as a gap", () => {
    // 10:00 and 10:15 measured; 10:05 and 10:10 have no row.
    const s = buildSlots([bucket(0), bucket(15)], T(0), T(20));
    expect(s.slots.map((x) => x.state)).toEqual(["exact", "gap", "gap", "exact"]);
    expect(s.gapCount).toBe(2);
  });

  it("never draws a gap as 0", () => {
    const s = buildSlots([bucket(0), bucket(15)], T(0), T(20));
    expect(s.slots.map(barValue)).toEqual([100, null, null, 100]);
  });

  it("claims nothing after the latest measured bucket", () => {
    const s = buildSlots([bucket(0), bucket(5)], T(0), T(30));
    expect(s.slots.at(-1)?.state).toBe("beyond");
    expect(s.gapCount).toBe(0);
    expect(s.slots.filter((x) => x.state === "beyond").every((x) => barValue(x) === null)).toBe(true);
    expect(collectedUpTo(s)).toBe(T(10));
  });

  it("counts the buckets before the first measured one, inside the range, as gaps", () => {
    const s = buildSlots([bucket(10)], T(0), T(15));
    expect(s.slots.map((x) => x.state)).toEqual(["gap", "gap", "exact"]);
  });

  it("flags a LOWER_BOUND bucket and keeps its events as a bound", () => {
    const s = buildSlots([bucket(0, { measurement: "LOWER_BOUND", events: 900 })], T(0), T(5));
    expect(s.slots[0].state).toBe("lower_bound");
    expect(s.lowerBoundCount).toBe(1);
    expect(barValue(s.slots[0])).toBe(900);
  });

  it("treats a FAILED row as not measured, not as its stored counts", () => {
    const s = buildSlots(
      [bucket(0), bucket(5, { measurement: "FAILED", events: 0 }), bucket(10)],
      T(0),
      T(15),
    );
    expect(s.slots.map(barValue)).toEqual([100, null, 100]);
    expect(s.slots[1].state).toBe("gap");
  });

  it("knows no width and no gap when the range holds no bucket", () => {
    const s = buildSlots([], T(0), T(60));
    expect(s).toMatchObject({ slots: [], bucketSeconds: null, gapCount: 0 });
    expect(collectedUpTo(s)).toBeNull();
  });
});

describe("slot helpers", () => {
  it("groups consecutive gaps into runs", () => {
    const s = buildSlots([bucket(0), bucket(20), bucket(25), bucket(40)], T(0), T(45));
    expect(gapRuns(s.slots)).toEqual([
      [1, 3],
      [6, 7],
    ]);
  });

  it("steps over gaps to the next measured bucket and stops at the ends", () => {
    const s = buildSlots([bucket(0), bucket(20)], T(0), T(25));
    expect(stepMeasured(s.slots, 0, 1)).toBe(4);
    expect(stepMeasured(s.slots, 4, 1)).toBeNull();
    expect(stepMeasured(s.slots, 0, -1)).toBeNull();
  });
});

describe("figures", () => {
  it("keeps unknown apart from a measured zero", () => {
    expect(formatEvents24h(null)).toBe("unknown");
    expect(formatEvents24h(0)).toBe("0");
    expect(formatCount(null)).toBe("—");
    expect(formatCount(0)).toBe("0");
  });

  it("states the admission counts in words and says refused senders are not stored", () => {
    const lines = admissionSentences(bucket(0));
    expect(lines).toHaveLength(4);
    expect(lines[0]).toMatch(/1 new sender was admitted/);
    expect(lines[3]).toMatch(/5 senders \(7 events\) are not tracked by name/);
  });
});

describe("chart helpers", () => {
  it("shades gap slots only", () => {
    const s = buildSlots([bucket(0), bucket(15)], T(0), T(25));
    expect(gapBand(s.slots, 10)).toEqual([null, 10, 10, null, null]);
  });

  it("rounds the axis maximum up, never down", () => {
    for (const v of [1, 7, 94, 1355, 2982, 18420]) expect(niceMax(v)).toBeGreaterThanOrEqual(v);
    expect(niceMax(0)).toBe(1);
  });

  it("drops the zone for a header that names it", () => {
    expect(formatSeen("2026-10-13T15:00:00Z")).toBe("10-13 15:00");
  });

  it("breaks a name after . and - only", () => {
    expect(breakParts("sw-core-a.lab.local")).toEqual(["sw-", "core-", "a.", "lab.", "local"]);
    expect(breakParts("unbroken")).toEqual(["unbroken"]);
  });

  it("explains a lower bound and adds nothing for an exact bucket", () => {
    expect(measurementText(bucket(0))).toBeNull();
    expect(measurementText(bucket(0, { measurement: "LOWER_BOUND" }))).toMatch(/truncated/);
  });
});

describe("emptyKind", () => {
  it("says nothing was collected before saying the queue is clear", () => {
    expect(emptyKind({ filtered: false, status: "NEW", anyMeasured: false })).toBe("never-collected");
    expect(emptyKind({ filtered: false, status: "NEW", anyMeasured: true })).toBe("clear");
    expect(emptyKind({ filtered: true, status: "NEW", anyMeasured: true })).toBe("filtered");
    expect(emptyKind({ filtered: false, status: "REVIEWED", anyMeasured: true })).toBe("none-in-status");
  });
});

describe("senderFailure", () => {
  it("names the permission on a 403 and says nothing changed", () => {
    const f = senderFailure(new ApiError(403, "no"));
    expect(f.kind).toBe("forbidden");
    expect(f.text).toContain("log_source:write");
    expect(f.text).toMatch(/Nothing was changed/);
  });

  it("says not changed on any other failure", () => {
    expect(senderFailure(new ApiError(500, "boom")).text).toMatch(/^Not changed/);
  });
});

describe("cadence label", () => {
  it("names the catch-all senders family", () => {
    expect(FAMILY_INFO.CATCHALL_SENDERS).toEqual({
      name: "Catch-all senders",
      scope: "Senders feeding SIM Generic and the Universal DSMs",
    });
    expect(Object.keys(FAMILY_INFO).slice(-3)).toEqual([
      "CATCHALL_SENDERS",
      "GEO_COLLECTION",
      "PROPERTY_WATCH",
    ]);
  });
});
