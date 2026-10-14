import { describe, expect, it } from "vitest";

import {
  columnsForWidth,
  compactLayout,
  deriveLayout,
  firstFreeSpot,
  moveWidget,
  newWidgetId,
  resizeWidget,
} from "./layout";
import { SOC_OVERVIEW } from "./templates";
import type { LayoutWidget } from "./types";

const lg = SOC_OVERVIEW.layout.widgets;

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

describe("compactLayout", () => {
  it("closes vertical gaps after a widget is removed", () => {
    const compacted = compactLayout([
      box("w_left", 0, 4, 6, 2),
      box("w_right", 6, 8, 6, 2),
    ]);
    expect(compacted.map((widget) => widget.y)).toEqual([0, 0]);
    noOverlap(compacted);
  });
});

describe("moveWidget", () => {
  it.each([
    ["left", 1, 2],
    ["right", 3, 2],
    ["up", 2, 1],
    ["down", 2, 3],
  ] as const)("moves %s by one cell", (direction, x, y) => {
    const moved = moveWidget([box("w_move", 2, 2, 3, 2)], "w_move", direction);
    expect(moved[0]).toMatchObject({ x, y });
  });

  it.each([
    ["left", box("w_move", 0, 2, 3, 2)],
    ["right", box("w_move", 9, 2, 3, 2)],
    ["up", box("w_move", 2, 0, 3, 2)],
  ] as const)("stops at the %s bound", (direction, widget) => {
    expect(moveWidget([widget], widget.id, direction)).toEqual([widget]);
  });

  it("resolves a collision and compacts around the moved widget", () => {
    const moved = moveWidget(
      [box("w_move", 0, 0, 3, 2), box("w_other", 3, 0, 3, 2)],
      "w_move",
      "right",
    );
    expect(moved.find((widget) => widget.id === "w_move")).toMatchObject({ x: 1, y: 0 });
    expect(moved.find((widget) => widget.id === "w_other")).toMatchObject({ x: 3, y: 2 });
    noOverlap(moved);
  });

  it("never crosses the 12-column edge", () => {
    const moved = moveWidget([box("w_move", 8, 0, 4, 2)], "w_move", "right");
    expect(moved[0].x + moved[0].w).toBeLessThanOrEqual(12);
  });
});

describe("resizeWidget", () => {
  const size = { w: 4, h: 4, minW: 2, minH: 2, maxW: 6, maxH: 6 };

  it.each([
    ["wider", 5, 4],
    ["narrower", 3, 4],
    ["taller", 4, 5],
    ["shorter", 4, 3],
  ] as const)("resizes %s by one cell", (direction, w, h) => {
    const resized = resizeWidget([box("w_size", 2, 0, 4, 4)], "w_size", direction, size);
    expect(resized[0]).toMatchObject({ w, h });
  });

  it.each([
    ["wider", box("w_size", 2, 0, 6, 4)],
    ["narrower", box("w_size", 2, 0, 2, 4)],
    ["taller", box("w_size", 2, 0, 4, 6)],
    ["shorter", box("w_size", 2, 0, 4, 2)],
  ] as const)("stops at the %s registry bound", (direction, widget) => {
    expect(resizeWidget([widget], widget.id, direction, size)).toEqual([widget]);
  });

  it("stops wider at the grid edge even when maxW has room", () => {
    const widget = box("w_size", 8, 0, 4, 4);
    expect(resizeWidget([widget], widget.id, "wider", size)).toEqual([widget]);
  });

  it("moves a collided neighbor and compacts it below the resized widget", () => {
    const resized = resizeWidget(
      [box("w_size", 0, 0, 3, 2), box("w_other", 3, 0, 3, 2)],
      "w_size",
      "wider",
      { ...size, w: 3 },
    );
    expect(resized.find((widget) => widget.id === "w_size")).toMatchObject({ w: 4, y: 0 });
    expect(resized.find((widget) => widget.id === "w_other")).toMatchObject({ y: 2 });
    noOverlap(resized);
  });

  it("keeps every resized widget inside 12 columns", () => {
    for (const direction of ["wider", "narrower", "taller", "shorter"] as const) {
      const resized = resizeWidget([box("w_size", 7, 0, 5, 4)], "w_size", direction, {
        ...size,
        maxW: 12,
      });
      expect(resized[0].x + resized[0].w).toBeLessThanOrEqual(12);
    }
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
