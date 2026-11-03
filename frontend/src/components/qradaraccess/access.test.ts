import { describe, expect, it } from "vitest";

import { entry, consoleOf, REFUSED } from "./fixtures";
import {
  anyRunning,
  changedFrom,
  orderEntries,
  syncWord,
  toneOf,
  verdictOf,
} from "./access";

describe("verdictOf", () => {
  it("is all ok only when every entry answered ok", () => {
    expect(verdictOf(consoleOf()).kind).toBe("allOk");
  });

  it("names the refused APIs, forbidden and not found alike", () => {
    const verdict = verdictOf(consoleOf({ entries: REFUSED }));
    expect(verdict.kind).toBe("refused");
    expect(verdict.count).toBe(4);
    expect(verdict.titles).toEqual([
      "Offenses",
      "Custom event properties",
      "Building blocks",
      "License pool",
    ]);
  });

  it("puts a 401 before every other status", () => {
    const verdict = verdictOf(
      consoleOf({
        entries: [entry({ status: "unauthorized", http_status: 401 }), ...REFUSED],
      }),
    );
    expect(verdict.kind).toBe("unauthorized");
  });

  it("is not checked yet when no entry has a status, never all ok", () => {
    const blank = consoleOf({
      last_checked_at: null,
      entries: [entry({ status: null, http_status: null, checked_at: null })],
    });
    expect(verdictOf(blank).kind).toBe("notChecked");
    expect(verdictOf(consoleOf({ entries: [] })).kind).toBe("notChecked");
  });

  it("does not call an unchecked entry reachable", () => {
    const partly = consoleOf({
      entries: [entry(), entry({ id: "b", title: "B", status: null, http_status: null })],
    });
    expect(verdictOf(partly)).toMatchObject({ kind: "partlyUnchecked", titles: ["B"] });
  });

  it("separates an API that did not answer from one that refused", () => {
    const silent = consoleOf({
      entries: [entry(), entry({ id: "b", title: "B", status: "error", http_status: 502 })],
    });
    expect(verdictOf(silent)).toMatchObject({ kind: "notAnswered", titles: ["B"] });
  });

  it("says nothing is checked on a console that is not applicable", () => {
    expect(verdictOf(consoleOf({ applicable: false, entries: [] })).kind).toBe("notApplicable");
  });
});

describe("orderEntries", () => {
  it("lists refused first, then not answered or unchecked, then ok, keeping catalog order", () => {
    const ids = orderEntries(REFUSED).map((e) => e.id);
    expect(ids).toEqual([
      "offenses",
      "custom_properties",
      "building_blocks",
      "license_pool",
      "ariel_searches",
      "system_about",
      "log_sources",
    ]);
  });

  it("does not change the input", () => {
    const before = REFUSED.map((e) => e.id);
    orderEntries(REFUSED);
    expect(REFUSED.map((e) => e.id)).toEqual(before);
  });
});

describe("toneOf and changedFrom", () => {
  it("is neutral for ok and never checked, error for 401, warning for the rest", () => {
    expect(toneOf("ok")).toBe("");
    expect(toneOf(null)).toBe("");
    expect(toneOf("unauthorized")).toBe("crit");
    for (const status of ["forbidden", "not_found", "unreachable", "error"] as const) {
      expect(toneOf(status)).toBe("warn");
    }
  });

  it("reports the previous status only when it differs", () => {
    expect(changedFrom(entry({ status: "forbidden", previous_status: "ok" }))).toBe("ok");
    expect(changedFrom(entry({ status: "forbidden", previous_status: "forbidden" }))).toBeNull();
    expect(changedFrom(entry({ previous_status: null }))).toBeNull();
  });
});

describe("anyRunning and syncWord", () => {
  it("counts a running check only on an applicable console", () => {
    expect(anyRunning([consoleOf({ check_running: true })])).toBe(true);
    expect(anyRunning([consoleOf({ check_running: true, applicable: false })])).toBe(false);
    expect(anyRunning([consoleOf()])).toBe(false);
  });

  it("words the known syncs and keeps an unknown name readable", () => {
    expect(syncWord("log_source_inventory")).toBe("log source inventory");
    expect(syncWord("health_cache")).toBe("QRadar health reads");
    expect(syncWord("new_thing")).toBe("new thing");
  });
});
