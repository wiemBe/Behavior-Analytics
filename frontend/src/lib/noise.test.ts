import { describe, expect, it } from "vitest";

import {
  changedKnobs,
  closesIncidents,
  formFromDetail,
  formIsUnchanged,
  formProblems,
  knobProblem,
  mergedCustomThresholds,
  previewKey,
  tuningFailure,
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
  it("links a row to its tuning page and carries only a non-default list state", () => {
    expect(noiseDetailHref(row)).toBe("/behavior/noise/a%20b");
    expect(noiseDetailHref(row, { window: 28, order: "incidents", offset: 0 })).toBe(
      "/behavior/noise/a%20b",
    );
    expect(noiseDetailHref(row, { window: 7, order: "alerts", offset: 50 })).toBe(
      "/behavior/noise/a%20b?window=7&order=alerts&offset=50",
    );
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

describe("the tuning form", () => {
  const base = {
    criticality: "HIGH",
    custom_thresholds: { open_after: 4, by_type: { VOLUME_SPIKE: { deviation_z: 9 } } },
    maintenance_mode: false,
    maintenance_until: null,
    business_hours_only: false,
    expected_interval_seconds: 300,
    effective_thresholds: {
      deviation_z: { value: 3.5, source: "default" },
      spike_ratio: { value: 2, source: "default" },
      drop_ratio: { value: 0.5, source: "default" },
      min_absolute_delta_events: { value: 100, source: "default" },
      open_after: { value: 4, source: "custom" },
      direction: { value: "both", source: "default" },
    },
    lab_mode: false,
    has_by_type: true,
  } as const;
  const cur = base as never;

  it("starts as the engine resolves it, with nothing changed", () => {
    const form = formFromDetail(cur);
    expect(form.values.deviation_z).toBe("3.5");
    expect(form.direction).toBe("both");
    expect(changedKnobs(form, cur)).toEqual({});
    expect(formIsUnchanged(form, cur)).toBe(true);
  });

  it("sends only what differs and keeps stored keys the form does not own", () => {
    const form = { ...formFromDetail(cur), direction: "spike" as const };
    form.values = { ...form.values, deviation_z: "4.5", spike_ratio: "2" };
    expect(changedKnobs(form, cur)).toEqual({ deviation_z: 4.5, direction: "spike" });
    expect(mergedCustomThresholds(form, cur)).toEqual({
      open_after: 4,
      by_type: { VOLUME_SPIKE: { deviation_z: 9 } },
      deviation_z: 4.5,
      direction: "spike",
    });
  });

  it("changes the preview key for every control, and for nothing else", () => {
    const form = formFromDetail(cur);
    const key = previewKey(form, cur);
    expect(previewKey({ ...form }, cur)).toBe(key);
    expect(previewKey({ ...form, window: 7 }, cur)).not.toBe(key);
    expect(previewKey({ ...form, criticality: "LOW" }, cur)).not.toBe(key);
    expect(previewKey({ ...form, direction: "drop" }, cur)).not.toBe(key);
    expect(previewKey({ ...form, values: { ...form.values, open_after: "3" } }, cur)).not.toBe(key);
  });

  it("holds the backend's bounds and whole-number rule", () => {
    const form = formFromDetail(cur);
    const v = (knob: Parameters<typeof knobProblem>[0], text: string) => knobProblem(knob, text, cur);
    expect(v("deviation_z", "0.4")).toBe("range");
    expect(v("deviation_z", "0.5")).toBeNull();
    expect(v("deviation_z", "20")).toBeNull();
    expect(v("deviation_z", "20.1")).toBe("range");
    expect(v("spike_ratio", "1")).toBe("range");
    expect(v("drop_ratio", "1")).toBe("range");
    expect(v("drop_ratio", "0.01")).toBeNull();
    expect(v("min_absolute_delta_events", "-1")).toBe("range");
    expect(v("min_absolute_delta_events", "1000001")).toBe("range");
    expect(v("open_after", "0")).toBe("range");
    expect(v("open_after", "11")).toBe("range");
    expect(v("open_after", "2.5")).toBe("whole");
    expect(v("open_after", "")).toBe("empty");
    expect(v("open_after", "x")).toBe("notNumber");
    expect(formProblems(form, cur)).toEqual({});
  });

  it("closes incidents only when the direction narrows to something new", () => {
    const form = formFromDetail(cur);
    expect(closesIncidents(form, cur)).toBe(false);
    expect(closesIncidents({ ...form, direction: "spike" }, cur)).toBe(true);
    const spike = { ...base, effective_thresholds: { ...base.effective_thresholds, direction: { value: "spike", source: "custom" } } } as never;
    expect(closesIncidents({ ...formFromDetail(spike) }, spike)).toBe(false);
    expect(closesIncidents({ ...formFromDetail(spike), direction: "both" }, spike)).toBe(false);
    expect(closesIncidents({ ...formFromDetail(spike), direction: "drop" }, spike)).toBe(true);
  });

  it("reads the field a 422 names, in both error shapes", () => {
    expect(
      tuningFailure([{ msg: "Value error, custom_thresholds.drop_ratio: Input should be less than 0.99" }], "x"),
    ).toEqual({ fields: { drop_ratio: "Input should be less than 0.99" }, general: null });
    expect(tuningFailure("custom_thresholds.direction: unknown", "x").fields.direction).toBe("unknown");
    expect(tuningFailure("something else", "x")).toEqual({ fields: {}, general: "something else" });
    expect(tuningFailure(undefined, "Unprocessable").general).toBe("Unprocessable");
    expect(tuningFailure("custom_thresholds.nope: bad", "x").general).toContain("bad");
  });
});
