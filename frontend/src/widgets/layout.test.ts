import { describe, expect, it } from "vitest";

import {
  columnsForWidth,
  deriveLayout,
  firstFreeSpot,
  newWidgetId,
} from "./layout";
import { SOC_OVERVIEW } from "./templates";
import type { LayoutWidget } from "./types";

const lg = SOC_OVERVIEW.layout.widgets;

function noOverlap(ws: LayoutWidget[]) {
  ws.forEach((a, i) => {
    for (const b of ws.slice(i + 1)) {
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      expect(apart, `${a.id} overlaps ${b.id}`).toBe(true);
    }
  });
}

describe("columnsForWidth", () => {
  it("is 12 from 900 px, 6 from 600 px, else 1", () => {
    expect(columnsForWidth(900)).toBe(12);
    expect(columnsForWidth(984)).toBe(12);
    expect(columnsForWidth(899.9)).toBe(6);
    expect(columnsForWidth(728)).toBe(6);
    expect(columnsForWidth(600)).toBe(6);
    expect(columnsForWidth(599)).toBe(1);
  });
});

describe("deriveLayout", () => {
  it("returns the stored layout in reading order at 12 columns", () => {
    const shuffled = [...lg].reverse();
    expect(deriveLayout(shuffled, 12).map((w) => w.id)).toEqual(lg.map((w) => w.id));
  });

  it("gives the 6-column layout the approved Phase 2 reflow", () => {
    const six = deriveLayout(lg, 6);
    expect(six.map((w) => w.id)).toEqual(lg.map((w) => w.id));
    expect(six.map((w) => [w.x, w.w])).toEqual([
      [0, 3], [3, 3], [0, 3], [3, 3], // the KPIs, two by two
      [0, 6], // fleet EPS takes the row
      [0, 3], [3, 3], // class beside top sources
      [0, 3], [3, 3], // severity beside health
      [0, 6], [0, 6], // the two tables
    ]);
    for (const w of six) expect(w.x + w.w).toBeLessThanOrEqual(6);
    noOverlap(six);
  });

  it("stacks every widget in one column, in order, without overlap", () => {
    const one = deriveLayout(lg, 1);
    expect(one.map((w) => w.id)).toEqual(lg.map((w) => w.id));
    for (const w of one) {
      expect(w.x).toBe(0);
      expect(w.w).toBe(1);
    }
    noOverlap(one);
  });

  it("keeps heights and does not change its input", () => {
    const before = JSON.stringify(lg);
    const six = deriveLayout(lg, 6);
    expect(six.map((w) => w.h)).toEqual(lg.map((w) => w.h));
    expect(JSON.stringify(lg)).toBe(before);
  });

  it("derives 6 and 1 columns from the stored 12 in turn", () => {
    for (const cols of [12, 6, 1] as const) {
      const out = deriveLayout(lg, cols);
      expect(out).toHaveLength(lg.length);
      for (const w of out) expect(w.x + w.w).toBeLessThanOrEqual(cols);
    }
  });
});

describe("firstFreeSpot", () => {
  const box = (id: string, x: number, y: number, w: number, h: number): LayoutWidget => ({
    id,
    type: "kpi.total_eps",
    x,
    y,
    w,
    h,
    range: null,
    config: {},
  });

  it("is the origin for an empty dashboard", () => {
    expect(firstFreeSpot([], 3, 4)).toEqual({ x: 0, y: 0 });
  });

  it("takes the leftmost free column of the top row first", () => {
    expect(firstFreeSpot([box("w_aaaa", 0, 0, 3, 4)], 3, 4)).toEqual({ x: 3, y: 0 });
  });

  it("goes to the next row when the row is full", () => {
    const full = [box("w_aaaa", 0, 0, 6, 4), box("w_bbbb", 6, 0, 6, 2)];
    expect(firstFreeSpot(full, 6, 4)).toEqual({ x: 6, y: 2 });
    expect(firstFreeSpot(full, 8, 4)).toEqual({ x: 0, y: 4 });
  });

  it("fills a gap left by a removed widget", () => {
    const gap = [box("w_aaaa", 0, 0, 3, 4), box("w_cccc", 6, 0, 6, 4)];
    expect(firstFreeSpot(gap, 3, 4)).toEqual({ x: 3, y: 0 });
  });

  it("never overlaps what is there", () => {
    const placed = [...lg];
    const spot = firstFreeSpot(placed, 4, 12);
    noOverlap([...placed, { ...box("w_new0", spot.x, spot.y, 4, 12) }]);
  });
});

describe("newWidgetId", () => {
  it("matches the layout's id pattern", () => {
    expect(newWidgetId(new Set())).toMatch(/^w_[a-z0-9]{4,12}$/);
  });
  it("skips an id that is taken", () => {
    const values = [0, 0.5];
    const taken = new Set([newWidgetId(new Set(), () => 0)]);
    const id = newWidgetId(taken, () => values.shift() ?? 0.9);
    expect(taken.has(id)).toBe(false);
  });
});
