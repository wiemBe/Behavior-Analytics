import { describe, expect, it } from "vitest";

import { point, profile, source, summary } from "./fixtures";
import {
  IDENTITY_EVENTS,
  bucketState,
  eventTotals,
  formatEvents,
  identityHref,
  incidentsBySource,
  namesIncomplete,
  namesOf,
  notCollectedWhy,
  parseEvent,
  parseRange,
  slotsIn,
  sortByAttention,
  windowOf,
} from "./identityFormat";

describe("bucketState", () => {
  it("has no bucket means not collected, never complete", () => {
    expect(bucketState(null)).toBe("NOT_COLLECTED");
  });
  it("maps each measurement and keeps a cut name list as partial", () => {
    const b = source().last_bucket!;
    expect(bucketState(b)).toBe("COMPLETE");
    expect(bucketState({ ...b, admission_truncated: true })).toBe("PARTIAL");
    expect(bucketState({ ...b, measurement: "TRUNCATED" })).toBe("TRUNCATED");
    expect(bucketState({ ...b, measurement: "NOT_COLLECTED", population: null })).toBe("NOT_COLLECTED");
  });
  it("does not trust a measurement word it does not know", () => {
    expect(bucketState({ ...source().last_bucket!, measurement: "WHOLE" })).toBe("NOT_COLLECTED");
  });
});

describe("notCollectedWhy", () => {
  it("is null only for an enabled profile validated for its current version", () => {
    expect(notCollectedWhy(profile())).toBeNull();
    expect(notCollectedWhy(profile({ enabled: false }))).toBe("disabled");
    expect(notCollectedWhy(profile({ validation_status: "INVALID" }))).toBe("unvalidated");
    expect(notCollectedWhy(profile({ validated_profile_version: 1 }))).toBe("stale");
    expect(notCollectedWhy(profile({ validated_profile_version: null }))).toBe("stale");
  });
});

describe("eventTotals", () => {
  const W = 4;
  it("sums whole buckets and counts the missing ones as unmeasured, not as zero", () => {
    const [t] = eventTotals(
      [point({ event_count: 10 }), point({ bucket_start: "2026-11-13T10:00:00Z", event_count: 5 })],
      [4625],
      W,
    );
    expect(t.total).toBe(15);
    expect(t.unmeasured).toBe(2);
    expect(t.atLeast).toBe(true);
  });
  it("is exact only when every slot was measured whole", () => {
    const rows = Array.from({ length: W }, (_, i) => point({ bucket_start: `2026-11-13T10:0${i}:00Z`, event_count: 1 }));
    const [t] = eventTotals(rows, [4625], W);
    expect(t).toMatchObject({ total: 4, atLeast: false, unmeasured: 0 });
  });
  it("has no figure at all when nothing was measured", () => {
    const [none] = eventTotals([], [4625], W);
    expect(none.total).toBeNull();
    expect(none.atLeast).toBe(false);
    const [uncollected] = eventTotals(
      [point({ measurement: "NOT_COLLECTED", event_count: null })],
      [4625],
      1,
    );
    expect(uncollected.total).toBeNull();
  });
  it("keeps a measured zero as zero", () => {
    const [t] = eventTotals([point({ event_count: 0 })], [4625], 1);
    expect(t.total).toBe(0);
    expect(t.atLeast).toBe(false);
  });
  it("makes a truncated bucket a floor", () => {
    const [t] = eventTotals([point({ measurement: "TRUNCATED", event_count: 50 })], [4625], 1);
    expect(t).toMatchObject({ total: 50, atLeast: true });
    const [unknown] = eventTotals([point({ measurement: "TRUNCATED", event_count: null })], [4625], 1);
    expect(unknown.total).toBeNull();
  });
  it("separates event ids", () => {
    const totals = eventTotals([point({ event_id: 4740, event_count: 3 })], [4625, 4740], 1);
    expect(totals.map((x) => x.total)).toEqual([null, 3]);
  });
});

describe("names", () => {
  const series = [
    point({ usernames: [{ rank: 1, username: "a", count: 5, truncated: false }, { rank: 2, username: "b", count: 3, truncated: true }] }),
    point({ bucket_start: "2026-11-13T10:00:00Z", usernames: [{ rank: 1, username: "a", count: 2, truncated: false }] }),
    point({ event_id: 4740, usernames: [{ rank: 1, username: "z", count: 9, truncated: false }] }),
  ];
  it("sums only stored rows of the asked event and flags a cut name", () => {
    expect(namesOf(series, 4625)).toEqual([
      { username: "a", stored: 7, buckets: 2, cut: false },
      { username: "b", stored: 3, buckets: 1, cut: true },
    ]);
    expect(namesOf(series, 1102)).toEqual([]);
  });
  it("reports an incomplete name search for the asked event only", () => {
    expect(namesIncomplete(series, 4625)).toBe(false);
    expect(namesIncomplete([point({ names_measurement: "TRUNCATED" })], 4625)).toBe(true);
    expect(namesIncomplete([point({ names_measurement: "NOT_COLLECTED" })], 4625)).toBe(true);
    expect(namesIncomplete([point({ admission_truncated: true })], 4625)).toBe(true);
    expect(namesIncomplete([point({ names_measurement: "TRUNCATED", event_id: 4740 })], 4625)).toBe(false);
  });
});

describe("window and URL", () => {
  it("parses only known ranges and events", () => {
    expect(parseRange("7d")).toBe("7d");
    expect(parseRange("30d")).toBe("24h");
    expect(parseRange(undefined)).toBe("24h");
    expect(parseEvent("4625")).toBe(4625);
    expect(parseEvent("4624")).toBeNull();
    expect(parseEvent("x")).toBeNull();
  });
  it("aligns the window to the bucket grid and never exceeds seven days", () => {
    const w = windowOf("7d", Date.parse("2026-11-13T10:07:31Z"));
    expect(w.end).toBe("2026-11-13T10:05:00.000Z");
    expect(w.endMs - w.startMs).toBe(7 * 86_400_000);
    expect(slotsIn(w.startMs, w.endMs, 300)).toBe(2016);
    expect(slotsIn(10, 10, 300)).toBe(0);
  });
  it("leaves defaults out of the address", () => {
    expect(identityHref("/x", {})).toBe("/x");
    expect(identityHref("/x", { range: "24h" })).toBe("/x");
    expect(identityHref("/x", { range: "7d", event: 4740 })).toBe("/x?range=7d&event=4740");
  });
});

describe("sources and incidents", () => {
  it("lists a failing or uncollected source before a complete one", () => {
    const ok = source({ profile: profile({ log_source_name: "a-ok" }) });
    const failing = source({
      profile: profile({ log_source_name: "z-fail" }),
      collection: { ...source().collection, consecutive_failures: 3 },
    });
    const off = source({ profile: profile({ log_source_name: "m-off", enabled: false }) });
    expect(sortByAttention([ok, off, failing]).map((s) => s.profile.log_source_name)).toEqual([
      "z-fail",
      "m-off",
      "a-ok",
    ]);
  });
  it("groups open incidents by source in severity order", () => {
    const m = incidentsBySource([
      summary({ severity: "LOW" }),
      summary({ id: "2", severity: "CRITICAL" }),
      summary({ id: "3", severity: "LOW" }),
      summary({ id: "4", log_source_id: null }),
    ]);
    expect([...m.keys()]).toHaveLength(1);
    expect(m.values().next().value).toEqual({
      total: 3,
      bySeverity: [
        { severity: "CRITICAL", count: 1 },
        { severity: "LOW", count: 2 },
      ],
    });
  });
});

describe("formatEvents", () => {
  it("is the dash for a missing figure and keeps a measured zero", () => {
    expect(formatEvents(null)).toBe("—");
    expect(formatEvents(undefined)).toBe("—");
    expect(formatEvents(0)).toBe("0");
    expect(formatEvents(1234)).toBe("1,234");
    expect(formatEvents(2.5)).toBe("2.5");
  });
});

it("knows the eight catalogue events and not 4624", () => {
  expect(IDENTITY_EVENTS).toHaveLength(8);
  expect((IDENTITY_EVENTS as readonly number[]).includes(4624)).toBe(false);
});
