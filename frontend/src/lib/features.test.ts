import { describe, expect, it } from "vitest";

import { entry, overview, row, sw } from "@/components/features/fixtures";
import {
  blockedBy,
  groupProblems,
  isStale,
  layoutOf,
  loadOf,
  needsConfirm,
  needsOf,
  refusalMessage,
  sourceLines,
  stateOf,
  stepsFor,
  storedKeys,
} from "./features";

const waf = layoutOf(row("waf"));
const rows = Object.fromEntries(overview().families.map((r) => [r.family, r]));
if (waf.kind !== "state") throw new Error("waf is a state family");

describe("state changes", () => {
  it("reads Off, Shadow and Alerting from the two switches", () => {
    expect(stateOf(rows.waf!, waf)).toBe("shadow");
    const ips = layoutOf(rows.ips!);
    if (ips.kind !== "state") throw new Error("ips");
    expect(stateOf(rows.ips!, ips)).toBe("alerting");
    const rule = layoutOf(rows.rule_match!);
    if (rule.kind !== "state") throw new Error("rule");
    // Alerting stored but inert is still Off: collection decides.
    expect(stateOf(rows.rule_match!, rule)).toBe("off");
  });

  it("sends alerting after collection when going from Off, and nothing for the same state", () => {
    expect(stepsFor("off", "alerting", waf).map((s) => s.key)).toEqual([
      "waf_collection_enabled",
      "waf_alerting_enabled",
    ]);
    expect(stepsFor("shadow", "alerting", waf)).toEqual([{ key: "waf_alerting_enabled", enabled: true }]);
    expect(stepsFor("alerting", "shadow", waf)).toEqual([{ key: "waf_alerting_enabled", enabled: false }]);
    expect(stepsFor("alerting", "off", waf)).toEqual([{ key: "waf_collection_enabled", enabled: false }]);
    expect(stepsFor("shadow", "shadow", waf)).toEqual([]);
  });

  it("asks before turning off or leaving alerting, not before turning on", () => {
    expect(needsConfirm("shadow", "off")).toBe(true);
    expect(needsConfirm("alerting", "shadow")).toBe(true);
    expect(needsConfirm("off", "shadow")).toBe(false);
    expect(needsConfirm("shadow", "alerting")).toBe(false);
  });

  it("blocks a switch only while the one it needs is off", () => {
    expect(blockedBy(rows.catchall!, "catchall_new_sender_alerts_enabled")).toBe("catchall_collection_enabled");
    expect(blockedBy(rows.waf!, "waf_outcome_collection_enabled")).toBeNull();
    expect(blockedBy(rows.catchall!, "catchall_collection_enabled")).toBeNull();
  });

  it("lays out the JDBC and source-pause families as one toggle each, with no Ariel estimate", () => {
    const jdbc = row("jdbc", {
      switches: { jdbc_collection_enabled: sw(false) },
      estimate: { upper_bound_searches_per_hour: null, reason: "NOT_COLLECTED", count: 0, count_unit: "enabled_consoles", enabled_consoles: 1 },
      cadence_seconds: null,
    });
    const pause = row("qradar_source_pause", { switches: { qradar_source_pause_enabled: sw(false) } });
    expect(layoutOf(jdbc)).toEqual({ kind: "toggles", keys: ["jdbc_collection_enabled"] });
    expect(layoutOf(pause)).toEqual({ kind: "toggles", keys: ["qradar_source_pause_enabled"] });
    expect(loadOf(jdbc)).toEqual({ kind: "notEstimated" });
  });

  it("gives an unknown family its own switches as toggles", () => {
    const unknown = row("newer", { switches: { x_enabled: sw(true) } });
    expect(layoutOf(unknown)).toEqual({ kind: "toggles", keys: ["x_enabled"] });
  });
});

describe("source, load, staleness", () => {
  it("shows the newest stored change alone, else each .env or default origin", () => {
    expect(sourceLines(rows.waf!)).toEqual([{ source: "db", by: "a.rivera", at: "2026-11-05T17:05:00Z" }]);
    expect(sourceLines(rows.identity!)).toEqual([{ source: "env", by: null, at: null }]);
    expect(sourceLines(rows.catchall!)).toEqual([{ source: "default", by: null, at: null }]);
  });

  it("resets alerting before collection", () => {
    expect(storedKeys(rows.ips!)).toEqual(["ips_alerting_enabled", "ips_collection_enabled"]);
  });

  it("never turns a missing bound into a zero", () => {
    expect(loadOf(rows.waf!)).toMatchObject({ kind: "bound", perHour: 560, count: 14, measured: 531 });
    expect(loadOf(rows.catchall!)).toEqual({ kind: "insufficient" });
    expect(loadOf(rows.custom_rules!)).toEqual({ kind: "notEstimated" });
    const noProfiles = row("waf", {
      estimate: { ...rows.waf!.estimate, upper_bound_searches_per_hour: null, reason: "NOT_COLLECTED", count: 0 },
    });
    expect(loadOf(noProfiles)).toEqual({ kind: "none", unit: "enabled_profiles" });
    expect(loadOf({ ...rows.waf!, measured_last_hour: null })).toMatchObject({ measured: null });
  });

  it("marks a collecting family stale after two cadences, never an off or never-run one", () => {
    const now = new Date("2026-11-05T19:05:00Z");
    const ips = layoutOf(rows.ips!);
    expect(isStale(rows.ips!, ips, now)).toBe(true); // 3 h on a 15 min cadence
    expect(isStale(rows.waf!, waf, now)).toBe(false);
    expect(isStale(rows.identity!, layoutOf(rows.identity!), now)).toBe(false);
    const off = row("ips", { ...rows.ips!, switches: { ips_collection_enabled: sw(false) } });
    expect(isStale(off, layoutOf(off), now)).toBe(false);
  });
});

describe("what the token reaches", () => {
  it("is advisory and says what it knows", () => {
    expect(needsOf(rows.waf!)).toEqual({ kind: "refused", titles: ["Custom event properties"] });
    expect(needsOf(rows.identity!)).toEqual({ kind: "notChecked" });
    expect(needsOf(rows.custom_rules!)).toEqual({ kind: "nothing" });
    expect(needsOf(rows.ips!)).toEqual({ kind: "reachable" });
    expect(needsOf({ ...rows.ips!, access: null })).toEqual({ kind: "adminOnly" });
    expect(needsOf({ ...rows.ips!, access: [] })).toEqual({ kind: "noConsole" });
    const silent = row("ips", { access: [{ instance_id: "i", entries: [entry({ status: "unreachable" })] }] });
    expect(needsOf(silent)).toEqual({ kind: "notAnswered" });
  });
});

describe("settings problems and refusals", () => {
  it("attaches a problem to the family its setting name starts with", () => {
    const grouped = groupProblems(["PW_X must not exceed PW_Y (2 > 1)", "WAF_A must exceed B", "OTHER_C"]);
    expect(grouped.byFamily).toEqual({
      property_watch: ["PW_X must not exceed PW_Y (2 > 1)"],
      waf: ["WAF_A must exceed B"],
    });
    expect(grouped.other).toEqual(["OTHER_C"]);
  });

  it("quotes the server's message, whatever shape the detail has", () => {
    expect(refusalMessage({ code: "c", message: "a; b" }, "x")).toBe("a; b");
    expect(refusalMessage("plain", "x")).toBe("plain");
    expect(refusalMessage({ code: "c" }, "fallback")).toBe("fallback");
    expect(refusalMessage(undefined, "fallback")).toBe("fallback");
  });
});

