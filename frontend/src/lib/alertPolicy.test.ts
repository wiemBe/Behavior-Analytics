import { describe, expect, it } from "vitest";

import { IPS_TYPES } from "@/components/ips/ipsLabels";
import { PW_TYPES } from "@/components/propertywatch/pwFormat";
import { WAF_TYPES } from "@/components/waf/wafLabels";
import { policy } from "@/app/admin/alert-policy/fixtures";
import { ApiError } from "@/lib/api";
import { createTextTranslator } from "@/i18n/textTranslator";
import messages from "../../messages/en";

import {
  allowsClass,
  detectorGroup,
  detectorsOf,
  fieldOf,
  holds,
  identityKey,
  isGone,
  knownConsoles,
  removeFailure,
  saveFailure,
  sortRows,
  touchesOutage,
} from "./alertPolicy";

const say = createTextTranslator("en", messages as unknown as Record<string, unknown>);

describe("the detector lists", () => {
  it("equal the lists the family pages already filter by", () => {
    expect([...detectorsOf("waf")]).toEqual(WAF_TYPES);
    expect([...detectorsOf("ips")]).toEqual(IPS_TYPES);
    expect([...detectorsOf("property_watch")]).toEqual(PW_TYPES);
  });

  it("list the nine Phase A detectors, NO_EVENTS first, and every one has a label", () => {
    const types = detectorsOf("log_source");
    expect(types).toHaveLength(9);
    expect(types[0]).toBe("NO_EVENTS");
    for (const family of ["log_source", "waf", "ips"] as const) {
      for (const type of detectorsOf(family)) {
        expect(Object.hasOwn(messages.enums.anomalyType, type)).toBe(true);
      }
    }
    for (const type of detectorsOf("property_watch")) {
      expect(Object.hasOwn(messages.enums.propertyWatchAnomalyType, type)).toBe(true);
    }
  });

  it("name the enum group a family's labels live in", () => {
    expect(detectorGroup("property_watch")).toBe("propertyWatchAnomalyType");
    expect(detectorGroup("waf")).toBe("anomalyType");
  });

  it("refuse a class scope for a property watch only", () => {
    expect(allowsClass("property_watch")).toBe(false);
    expect(allowsClass("log_source")).toBe(true);
    expect(allowsClass("waf")).toBe(true);
    expect(allowsClass("ips")).toBe(true);
  });
});

describe("sortRows", () => {
  it("puts SOURCE before CLASS, then orders by name", () => {
    const rows = [
      policy({ id: "c-dns", scope_value: "DNS" }),
      policy({ id: "s-b", scope_kind: "SOURCE", scope_value: "b", source_name: "beta" }),
      policy({ id: "c-fw", scope_value: "FIREWALL" }),
      policy({ id: "s-a", scope_kind: "SOURCE", scope_value: "a", source_name: "Alpha" }),
    ];
    expect(sortRows(rows).map((r) => r.id)).toEqual(["s-a", "s-b", "c-dns", "c-fw"]);
  });

  it("puts all detectors before one detector and all consoles before one console, and keeps the input", () => {
    const one = policy({ id: "one", detector_type: "VOLUME_DROP" });
    const all = policy({ id: "all", detector_type: null });
    const input = [one, all];
    expect(sortRows(input).map((r) => r.id)).toEqual(["all", "one"]);
    expect(input.map((r) => r.id)).toEqual(["one", "all"]);
    const global = policy({ id: "g" });
    const local = policy({ id: "l", instance_id: "i-1", instance_name: "lab" });
    expect(sortRows([local, global]).map((r) => r.id)).toEqual(["g", "l"]);
  });
});

describe("row facts", () => {
  it("touchesOutage: every log-source detector, NO_EVENTS, and nothing else", () => {
    expect(touchesOutage({ family: "log_source", detector_type: null })).toBe(true);
    expect(touchesOutage({ family: "log_source", detector_type: "NO_EVENTS" })).toBe(true);
    expect(touchesOutage({ family: "log_source", detector_type: "VOLUME_DROP" })).toBe(false);
    expect(touchesOutage({ family: "waf", detector_type: null })).toBe(false);
  });

  it("isGone: only a SOURCE row with no name", () => {
    expect(isGone({ scope_kind: "SOURCE", source_name: null })).toBe(true);
    expect(isGone({ scope_kind: "SOURCE", source_name: "x" })).toBe(false);
    expect(isGone({ scope_kind: "CLASS", source_name: null })).toBe(false);
  });

  it("identityKey differs in each of the five identity fields and ignores the mode", () => {
    const base = policy();
    const keys = new Set([
      identityKey(base),
      identityKey({ ...base, instance_id: "i" }),
      identityKey({ ...base, scope_kind: "SOURCE" }),
      identityKey({ ...base, scope_value: "DNS" }),
      identityKey({ ...base, family: "waf" }),
      identityKey({ ...base, detector_type: null }),
    ]);
    expect(keys.size).toBe(6);
    const digested = policy({ ...base, mode: "DIGEST" });
    expect(identityKey(digested)).toBe(identityKey(base));
  });

  it("knownConsoles lists each named console once and none for global rows", () => {
    const rows = [
      policy(),
      policy({ instance_id: "i-1", instance_name: "lab" }),
      policy({ instance_id: "i-1", instance_name: "lab" }),
      policy({ instance_id: "i-2", instance_name: null }),
    ];
    expect(knownConsoles(rows)).toEqual([
      { id: "i-1", name: "lab" },
      { id: "i-2", name: null },
    ]);
  });

  it("holds accepts the exact grant or the administrator wildcard", () => {
    expect(holds(["admin:outage"], "admin:outage")).toBe(true);
    expect(holds(["admin:*"], "admin:outage")).toBe(true);
    expect(holds(["read:alerts"], "admin:outage")).toBe(false);
    expect(holds([], "admin:outage")).toBe(false);
  });
});

describe("failures", () => {
  it("fieldOf maps the server's validation sentences to a field", () => {
    expect(fieldOf("unknown detector for family waf: NO_EVENTS")).toBe("detector");
    expect(fieldOf("unknown instance_id")).toBe("console");
    expect(fieldOf("scope_value is not a known log source")).toBe("scope");
    expect(fieldOf("unknown source class: X")).toBe("scope");
    expect(fieldOf("property watch has no class scope")).toBe("scope");
    expect(fieldOf("something else")).toBeNull();
  });

  it("saveFailure: 403 names the permission, 422 keeps the server's sentence at its field", () => {
    const forbidden = saveFailure(new ApiError(403, "no"), say);
    expect(forbidden.kind).toBe("forbidden");
    expect(forbidden.text).toContain("admin:outage");
    const invalid = saveFailure(
      new ApiError(422, "unknown detector for family waf: NO_EVENTS"),
      say,
    );
    expect(invalid.text).toBe("unknown detector for family waf: NO_EVENTS");
    expect(invalid.field).toBe("detector");
    const other = saveFailure(new ApiError(500, "boom"), say);
    expect(other.field).toBeUndefined();
    expect(other.text).toMatch(/^The policy was not saved: /);
  });

  it("removeFailure: 404 marks the list stale, 403 names the permission", () => {
    expect(removeFailure(new ApiError(404, "x"), say)).toMatchObject({ gone: true });
    const forbidden = removeFailure(new ApiError(403, "x"), say);
    expect(forbidden).toMatchObject({ kind: "forbidden", gone: false });
    expect(forbidden.text).toContain("admin:outage");
    expect(removeFailure(new ApiError(500, "x"), say).text).toMatch(/^The policy was not removed: /);
  });
});
