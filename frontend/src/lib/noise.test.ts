import { describe, expect, it } from "vitest";

import {
  isJudged,
  isNotMonitored,
  noiseDetailHref,
  noiseHref,
  noisePaginationParams,
  parseNoiseQuery,
  sourceAlertsHref,
  sourceHref,
} from "./noise";

describe("the noisy-sources query", () => {
  it("defaults to 28 days, incidents, first page", () => {
    expect(parseNoiseQuery({})).toEqual({ window: 28, order: "incidents", offset: 0 });
  });

  it("round-trips window, order and offset through the URL", () => {
    const q = parseNoiseQuery({ window: "7", order: "noise_share", offset: "50" });
    expect(q).toEqual({ window: 7, order: "noise_share", offset: 50 });
    expect(noiseHref(q)).toBe("/behavior/noise?window=7&order=noise_share&offset=50");
    expect(noisePaginationParams(q)).toEqual({ window: "7", order: "noise_share" });
  });

  it("leaves defaults out of a link", () => {
    expect(noiseHref(parseNoiseQuery({}))).toBe("/behavior/noise");
    expect(noiseHref(parseNoiseQuery({ window: "7" }), { window: 28, offset: 0 })).toBe("/behavior/noise");
  });

  it("drops values it does not know instead of forwarding them", () => {
    expect(parseNoiseQuery({ window: "14", order: "name", offset: "-3" })).toEqual({
      window: 28,
      order: "incidents",
      offset: 0,
    });
    expect(parseNoiseQuery({ offset: "abc" }).offset).toBe(0);
  });
});

describe("the row helpers", () => {
  const row = { log_source_id: "a b" };
  it("links the source and its alerts", () => {
    expect(sourceHref(row)).toBe("/behavior/sources/a%20b");
    expect(sourceAlertsHref(row)).toBe("/alerts?log_source_id=a%20b");
  });
  it("has no detail link until 17b adds the route", () => {
    expect(noiseDetailHref(row)).toBeNull();
  });
  it("never calls an unmonitored or unreliable source judged", () => {
    const base = { monitoring_enabled: true, judgement: "JUDGED", judged: true } as never;
    expect(isNotMonitored(base)).toBe(false);
    expect(isJudged(base)).toBe(true);
    expect(isNotMonitored({ monitoring_enabled: false, judgement: "JUDGED" })).toBe(true);
    expect(isJudged({ monitoring_enabled: true, judgement: "INSUFFICIENT_DATA", judged: false } as never)).toBe(false);
    expect(isJudged({ monitoring_enabled: true, judgement: "JUDGED", judged: false } as never)).toBe(false);
  });
});
