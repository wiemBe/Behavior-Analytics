import { describe, expect, it } from "vitest";

import { ema } from "./ema";
import { emaColours, emaDrawn, emaLinesOf, emaSeries, emaTooltipRows } from "./emaSeries";

const COLOURS = emaColours({ ema3: "c3", ema5: "c5", ema10: "c10", ema20: "c20" });

describe("emaLinesOf", () => {
  it("is one line per enabled period, in the order given, aligned with the input", () => {
    const lines = emaLinesOf([1, 2, 3, 4, 5, 6], [5, 3]);
    expect(lines.map((l) => l.period)).toEqual([5, 3]);
    expect(lines[1].values).toEqual(ema([1, 2, 3, 4, 5, 6], 3));
    expect(emaLinesOf([1, 2, 3], [])).toEqual([]);
  });

  it("ends at a null and never bridges it", () => {
    const [line] = emaLinesOf([1, 2, 3, 4, null, 1, 2, 3], [3]);
    expect(line.values[4]).toBeNull();
    expect(line.values.slice(5, 7)).toEqual([null, null]);
    expect(line.values[7]).toBeCloseTo(2, 10);
  });
});

describe("emaDrawn", () => {
  it("is false for a line that never warmed up", () => {
    expect(emaDrawn(emaLinesOf([1, 2], [3])[0])).toBe(false);
    expect(emaDrawn(emaLinesOf([1, 2, 3], [3])[0])).toBe(true);
  });
});

describe("emaSeries", () => {
  const slots = [100, 200, 300, 400];
  const [line] = emaLinesOf([2, 4, 6, null], [3]);

  it("is thin, solid, markerless, unbridged, under z 3 and coloured by period", () => {
    const [s] = emaSeries([line], slots, COLOURS);
    expect(s.name).toBe("EMA 3");
    expect(s.connectNulls).toBe(false);
    expect(s.showSymbol).toBe(false);
    expect(s.lineStyle).toEqual({ color: "c3", width: 1 });
    expect(s.z).toBe(2);
    expect(s.data).toEqual([[100, null], [200, null], [300, 4], [400, null]]);
  });

  it("scales the values and places them on the panel asked for, keeping nulls null", () => {
    const [s] = emaSeries([line], slots, COLOURS, 100, { xAxisIndex: 0, yAxisIndex: 1 });
    expect(s.yAxisIndex).toBe(1);
    expect(s.data[2]).toEqual([300, 400]);
    expect(s.data[3][1]).toBeNull();
  });

  it("maps each period to its own colour", () => {
    const lines = emaLinesOf([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], [3, 5, 10, 20]);
    expect(emaSeries(lines, [], COLOURS).map((s) => s.lineStyle.color)).toEqual(["c3", "c5", "c10", "c20"]);
  });
});

describe("emaTooltipRows", () => {
  it("has a row only where the line has a value", () => {
    const [line] = emaLinesOf([2, 4, 6, null], [3]);
    const row = (p: number, v: number) => `EMA ${p}: ${v}`;
    expect(emaTooltipRows([line], 2, row)).toEqual(["EMA 3: 4"]);
    expect(emaTooltipRows([line], 1, row)).toEqual([]);
    expect(emaTooltipRows([line], 3, row)).toEqual([]);
    expect(emaTooltipRows([line], -1, row)).toEqual([]);
  });
});
