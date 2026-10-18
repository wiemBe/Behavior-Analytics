import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { catalog, watch } from "./fixtures";
import {
  DETECTORS,
  classifyWatchError,
  createWatchBody,
  draftFromWatch,
  draftReasons,
  pinReason,
  propertyApplicability,
  propertyWatchAccessFrom,
  updateWatchBody,
  watchSummary,
} from "./authoring";

describe("property-watch authoring semantics", () => {
  it("recognises only the writer capability or admin wildcard", () => {
    const caller = { subject: "a", email: null, permissions: ["read:*"], effective_permissions: ["read:property_watches"] };
    expect(propertyWatchAccessFrom(caller)).toBe("reader");
    expect(propertyWatchAccessFrom({ ...caller, permissions: ["property_watch:write"] })).toBe("writer");
    expect(propertyWatchAccessFrom({ ...caller, permissions: ["admin:*"] })).toBe("writer");
  });

  it("builds a deterministic summary from the form, never from free text", () => {
    expect(watchSummary(draftFromWatch(watch()))).toBe(
      "Tracks username on DMZ firewalls (37 sources), counted on collected 5-minute buckets; 1 value always counted; value spike, new value detection on.",
    );
  });

  it("creates only a disabled definition and never accepts AQL", () => {
    const body = createWatchBody(draftFromWatch(watch()));
    expect(body).not.toHaveProperty("enabled");
    expect(body).not.toHaveProperty("aql");
    expect(JSON.stringify(body).toLowerCase()).not.toContain("select ");
    expect(updateWatchBody(draftFromWatch(watch({ enabled: true })), watch({ enabled: true }))).toEqual(
      expect.objectContaining({ expected_updated_at: watch().updated_at, enabled: false }),
    );
  });

  it("pins every local guard and returns every reason", () => {
    const draft = draftFromWatch(watch());
    const reasons = draftReasons(
      {
        ...draft,
        name: "",
        scopeId: "",
        properties: [
          { kind: "CUSTOM_PROPERTY", name: "bad'name" },
          { kind: "CUSTOM_PROPERTY", name: "bad'name" },
        ],
        pins: [[" padded ", "ok"], [" padded ", "ok"]],
        detectors: [],
      },
      catalog(),
    );
    expect(reasons).toEqual(expect.arrayContaining([
      "Name is required.",
      "Choose a log source group.",
      "A watch cannot name the same property twice.",
      "A value is pinned twice.",
      "Choose at least one detector.",
    ]));
    expect(reasons.some((reason) => reason.includes("Quotes"))).toBe(true);
  });

  it("validates quotes, backslashes, controls, padding and length inline", () => {
    for (const value of ["O'Brien", 'say "no"', "a\\b", " padded", "padded ", "line\nbreak"]) {
      expect(pinReason(value, 255)).toMatch(/cannot be pinned/);
    }
    expect(pinReason("x".repeat(256), 255)).toMatch(/at most 255/);
    expect(pinReason("DENY", 255)).toBeNull();
  });

  it("marks a typed name outside the catalog without treating the catalog as a gate", () => {
    expect(
      propertyApplicability(
        { kind: "CUSTOM_PROPERTY", name: "Typed Name" },
        {
          status: "AVAILABLE", fetched_at: null, scope_type_ids: [1, 2], properties: [], normalized_fields: ["username"], detail: null,
        },
      ),
    ).toMatch(/Not in the catalog/);
  });
});

describe("property-watch API refusals", () => {
  it("preserves every 422 reason exactly", () => {
    expect(
      classifyWatchError(
        new ApiError(422, "invalid", { message: "invalid", reasons: ["first", "second"] }),
      ),
    ).toEqual({ kind: "reasons", reasons: ["first", "second"] });
  });

  it("distinguishes version, dependency, capacity and rate conflicts", () => {
    expect(
      classifyWatchError(
        new ApiError(409, "Conflict", {
          message: "watch version conflict",
          current_version: "2026-10-18T08:00:00+00:00",
          updated_by: "grace",
        }),
      ),
    ).toMatchObject({ kind: "version", updatedBy: "grace" });
    expect(
      classifyWatchError(
        new ApiError(409, "Conflict", {
          message: "cannot delete",
          rules: [{ id: "rule-1", name: "Deny flood" }],
        }),
      ),
    ).toEqual({
      kind: "dependencies",
      message: "cannot delete",
      rules: [{ id: "rule-1", name: "Deny flood" }],
    });
    expect(classifyWatchError(new ApiError(409, "cap", "at most 10 watches"))).toEqual({
      kind: "conflict",
      detail: "at most 10 watches",
    });
    expect(classifyWatchError(new ApiError(429, "Too Many Requests", "5 per 1 minute"))).toEqual({
      kind: "rate",
      detail: "5 per 1 minute",
    });
  });
});

describe("detector claims", () => {
  it("states the drop as an exact count below the range, not an 'exactly below' slip", () => {
    expect(DETECTORS.PW_VALUE_DROP.claim).toBe("A value's exact count fell below its seasonal expected range.");
    expect(DETECTORS.PW_NEW_COMBINATION.claim).toContain("each seen before");
  });
});
