import { describe, expect, it } from "vitest";

import { offenseRow, page } from "./fixtures";
import { magnitudeSegments, recentOffenses } from "./recentOffenses";

describe("magnitudeSegments", () => {
  it("rounds into 0-10 and treats null as unknown, not zero", () => {
    expect(magnitudeSegments(null)).toBeNull();
    expect(magnitudeSegments(undefined)).toBeNull();
    expect(magnitudeSegments(Number.NaN)).toBeNull();
    expect(magnitudeSegments(0)).toBe(0);
    expect(magnitudeSegments(6.6)).toBe(7);
    expect(magnitudeSegments(14)).toBe(10);
    expect(magnitudeSegments(-2)).toBe(0);
  });
});

describe("recentOffenses", () => {
  it("lists the open offenses with a link to each", () => {
    const v = recentOffenses(page([offenseRow({ qradar_offense_id: 42, magnitude: 8 })], 12), page([offenseRow()]));
    expect(v.rows[0]).toMatchObject({ id: 42, segments: 8, href: "/offenses/42" });
    expect(v.shown).toBe("Showing the latest 1 of 12");
    expect(v.qualifiers).toEqual([]);
  });

  it("keeps a null magnitude null so no bar is drawn", () => {
    const v = recentOffenses(page([offenseRow({ magnitude: null })]), page([offenseRow()]));
    expect(v.rows[0].magnitude).toBeNull();
    expect(v.rows[0].segments).toBeNull();
  });

  it("is NOT_COLLECTED when the probe finds no snapshot at all", () => {
    const v = recentOffenses(page([]), page([]));
    expect(v.qualifiers.map((q) => q.kind)).toEqual(["NOT_COLLECTED"]);
    expect(v.empty).toBeNull();
  });

  it("says no open offenses only when a snapshot exists", () => {
    const v = recentOffenses(page([]), page([offenseRow({ status: "CLOSED" })], 3));
    expect(v.empty).toBe("No open offenses in the latest snapshot.");
    expect(v.qualifiers).toEqual([]);
  });

  it("fails closed when the probe is missing", () => {
    const v = recentOffenses(page([]), null);
    expect(v.empty).toBeNull();
    expect(v.qualifiers[0]).toMatchObject({ kind: "NOT_COLLECTED" });
  });
});
