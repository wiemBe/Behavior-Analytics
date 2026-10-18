import { describe, expect, it } from "vitest";

import { catalog, iso, value, watch } from "@/components/propertywatch/fixtures";

import { ageSeconds, earlyState, isStale, PW_STALE_AFTER_SECONDS, widgetSelection } from "./pwWidgetState";

describe("earlyState", () => {
  it("lets a collected, applicable watch through", () => {
    expect(earlyState(watch(), catalog())).toBeNull();
    expect(earlyState(watch(), null)).toBeNull();
  });

  it("collection off outranks everything and names the setting", () => {
    const s = earlyState(watch({ status: "OK" }), catalog({ collection_enabled: false }));
    expect(s?.kind).toBe("not_collected");
    expect(s && "reason" in s && s.reason).toContain("PW_COLLECTION_ENABLED");
  });

  it("NOT_ENABLED without the catalog still names the setting", () => {
    const s = earlyState(watch({ status: "NOT_ENABLED" }), null);
    expect(s && "reason" in s && s.reason).toContain("PW_COLLECTION_ENABLED");
  });

  it.each(["SCOPE_STALE", "SCOPE_TOO_LARGE", "SCOPE_EMPTY"] as const)("%s is not_collected", (status) => {
    expect(earlyState(watch({ status, status_reason: "because" }), catalog())?.kind).toBe("not_collected");
  });

  it("uses the API's reason when it sends one", () => {
    const s = earlyState(watch({ status: "SCOPE_STALE", status_reason: "synced 3 d ago" }), null);
    expect(s && "reason" in s && s.reason).toContain("synced 3 d ago");
  });

  it("no bucket is empty, with the creation time in UTC, never a zero", () => {
    const s = earlyState(watch({ coverage: null, status: null }), catalog());
    expect(s?.kind).toBe("empty");
    expect(s && "reason" in s && s.reason).toContain("2026-10-10 09:00:00 UTC");
  });

  it("NOT_APPLICABLE with a bucket is no_data and carries the coverage", () => {
    const s = earlyState(
      watch({
        status: "NOT_APPLICABLE",
        coverage: { bucket_start: iso(11), total_events: 10, parsed_events: 0, ratio: 0, measurement: "EXACT" },
      }),
      catalog(),
    );
    expect(s?.kind).toBe("no_data");
  });

  it("an unknown coverage ratio is an em dash, not 0 %", () => {
    const s = earlyState(
      watch({
        status: "NOT_APPLICABLE",
        coverage: { bucket_start: iso(11), total_events: null, parsed_events: null, ratio: null, measurement: "EXACT" },
      }),
      catalog(),
    );
    expect(s && "reason" in s && s.reason).toContain("coverage —");
    expect(s && "reason" in s && s.reason).not.toContain("0 %");
  });
});

describe("widgetSelection", () => {
  it("is pinned first, then by events, at most five", () => {
    const items = [
      value("a", "a", { events: 1 }),
      value("b", "b", { events: 9 }),
      value("p", "p", { pinned: true, events: null }),
      value("c", "c", { events: 5 }),
      value("d", "d", { events: 4 }),
      value("e", "e", { events: 3 }),
      value("f", "f", { events: 2 }),
    ];
    expect(widgetSelection(items)).toEqual(["p", "b", "c", "d", "e"]);
  });

  it("skips a null and a zero reading, and breaks ties by id", () => {
    const items = [
      value("z", "z", { events: 0 }),
      value("n", "n", { events: null }),
      value("y", "y", { events: 7 }),
      value("x", "x", { events: 7 }),
    ];
    expect(widgetSelection(items)).toEqual(["x", "y"]);
  });

  it("lets pinned values take all five places", () => {
    const items = Array.from({ length: 7 }, (_, i) => value(`p${i}`, `p${i}`, { pinned: true }));
    expect(widgetSelection(items)).toHaveLength(5);
  });
});

describe("staleness", () => {
  const now = new Date(iso(11));
  it("age is null for a missing, invalid or future bucket", () => {
    expect(ageSeconds(null, now)).toBeNull();
    expect(ageSeconds("nope", now)).toBeNull();
    expect(ageSeconds(iso(20), now)).toBeNull();
    expect(ageSeconds(iso(10), now)).toBe(300);
  });

  it("is stale past twice the default cadence, not at it, or when the backend says STALE", () => {
    expect(isStale({ status: "OK" }, PW_STALE_AFTER_SECONDS)).toBe(false);
    expect(isStale({ status: "OK" }, PW_STALE_AFTER_SECONDS + 1)).toBe(true);
    expect(isStale({ status: "OK" }, null)).toBe(false);
    expect(isStale({ status: "STALE" }, 60)).toBe(true);
  });
});
