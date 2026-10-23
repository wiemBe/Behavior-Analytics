import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  SCHEMA,
  catalog,
  profile,
  status,
} from "@/components/fieldmapping/fixtures";
import {
  assignValue,
  assignmentOf,
  attentionRank,
  catalogNote,
  checkPropertyName,
  collectionState,
  conflictInfo,
  conflictMessage,
  draftChanges,
  draftErrors,
  draftFromDefaults,
  draftFromProfile,
  draftToInput,
  emptyDraft,
  enableBlockReason,
  fieldForMessage,
  fieldStatusInfo,
  formatCount,
  formatShare,
  historyChanges,
  pickPrefillSource,
  reasonText,
  serverMessage,
  resolveInstanceId,
  sortByAttention,
  validationReason,
  validationState,
  valueMapChanges,
  valueRows,
} from "./fieldMapping";

describe("formatShare", () => {
  it("renders a missing measurement as a dash, never as 0 %", () => {
    expect(formatShare(null)).toBe("—");
    expect(formatShare(undefined)).toBe("—");
    expect(formatShare(Number.NaN)).toBe("—");
  });
  it("renders a measured zero as 0 %", () => {
    expect(formatShare(0)).toBe("0.0\u00A0%");
  });
  it("rounds to one decimal and drops a trailing .0", () => {
    expect(formatShare(0.62)).toBe("62.0\u00A0%");
    expect(formatShare(0.9951)).toBe("99.5\u00A0%");
    expect(formatShare(1)).toBe("100.0\u00A0%");
  });
  it("does not round a tiny positive share down to zero", () => {
    expect(formatShare(0.0003)).toBe("<0.1\u00A0%");
  });
});

describe("formatCount", () => {
  it("keeps 0 and dashes null", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(null)).toBe("—");
    expect(formatCount(10234)).toBe("10,234");
  });
});

describe("checkPropertyName", () => {
  const p = SCHEMA.property_name_pattern;
  it("accepts names the pattern allows", () => {
    expect(checkPropertyName("WAF_Action", p)).toBeNull();
    expect(checkPropertyName("Request URI", p)).toBeNull();
  });
  it("rejects names it does not", () => {
    expect(checkPropertyName("a;b", p)).toMatch(/not an allowed property name/i);
    expect(checkPropertyName("-lead", p)).not.toBeNull();
    expect(checkPropertyName("x".repeat(65), p)).not.toBeNull();
  });
  it("leaves an empty name to the required check", () => {
    expect(checkPropertyName("  ", p)).toBeNull();
  });
  it("makes no client check when the pattern cannot compile", () => {
    expect(checkPropertyName("anything;", "(")).toBeNull();
  });
});

describe("draftErrors", () => {
  it("requires required fields and checks the pattern of mapped ones", () => {
    const d = draftFromProfile(SCHEMA, profile({ mapping: { URL: "", METHOD: "m;x" } }));
    const e = draftErrors(SCHEMA, d);
    expect(e.URL).toMatch(/required/i);
    expect(e.METHOD).toMatch(/not an allowed/i);
    expect(e.VIRTUAL_HOST).toBeUndefined();
  });
  it("rejects values assigned while the action property is unmapped", () => {
    const d = draftFromProfile(SCHEMA, profile({ mapping: { URL: "URL", METHOD: "M", ACTION: null } }));
    expect(draftErrors(SCHEMA, d)["@values"]).toMatch(/map the property first/i);
  });
  it("rejects a value in both lists", () => {
    const d = draftFromProfile(SCHEMA, profile({ action_value_map: { BLOCKED: ["X"], ALLOWED: ["X"] } }));
    expect(draftErrors(SCHEMA, d)["@values"]).toMatch(/both lists/i);
  });
  it("passes a complete mapping", () => {
    expect(draftErrors(SCHEMA, draftFromProfile(SCHEMA, profile()))).toEqual({});
  });
});

describe("draft conversion", () => {
  it("sends blanks as null and trims", () => {
    const d = draftFromProfile(SCHEMA, profile());
    d.mapping.URL = "  request_uri ";
    d.mapping.ACTION = "";
    d.productLabel = "  ";
    d.actionMap.BLOCKED = [" DENY ", ""];
    const body = draftToInput(SCHEMA, d, 4);
    expect(body.mapping.URL).toBe("request_uri");
    expect(body.mapping.ACTION).toBeNull();
    expect(body.product_label).toBeNull();
    expect(body.action_value_map.BLOCKED).toEqual(["DENY"]);
    expect(body.expected_profile_version).toBe(4);
  });
  it("seeds a new draft from the schema defaults", () => {
    const d = draftFromDefaults(SCHEMA);
    expect(d.mapping.URL).toBe("URL");
    expect(d.mapping.VIRTUAL_HOST).toBe("");
    expect(d.actionMap).toEqual({ BLOCKED: [], ALLOWED: [] });
  });
});

describe("draftChanges", () => {
  const base = draftFromProfile(SCHEMA, profile());
  it("lists each changed field with its old and new property", () => {
    const next = structuredClone(base);
    next.mapping.URL = "request_uri";
    next.mapping.VIRTUAL_HOST = "Host";
    const c = draftChanges(SCHEMA, base, next);
    expect(c.fields).toEqual([
      { key: "URL", label: "URL path", from: "URL", to: "request_uri" },
      { key: "VIRTUAL_HOST", label: "Virtual host", from: null, to: "Host" },
    ]);
    expect(c.mappingChanged).toBe(true);
    expect(c.total).toBe(2);
  });
  it("counts a label-only change apart from a mapping change", () => {
    const next = structuredClone(base);
    next.productLabel = "Other";
    const c = draftChanges(SCHEMA, base, next);
    expect(c.labelChanged).toBe(true);
    expect(c.mappingChanged).toBe(false);
    expect(c.total).toBe(1);
  });
  it("sees no change in whitespace", () => {
    const next = structuredClone(base);
    next.mapping.URL = " URL ";
    expect(draftChanges(SCHEMA, base, next).total).toBe(0);
  });
  it("treats a value-map edit as a mapping change", () => {
    const next = structuredClone(base);
    next.actionMap.BLOCKED.push("DENY");
    const c = draftChanges(SCHEMA, base, next);
    expect(c.mappingChanged).toBe(true);
    expect(c.values).toEqual([{ outcome: "BLOCKED", added: ["DENY"], removed: [] }]);
  });
  it("diffs a new profile against an empty draft", () => {
    const c = draftChanges(SCHEMA, emptyDraft(SCHEMA), draftFromDefaults(SCHEMA));
    expect(c.fields.map((f) => f.key)).toEqual(["URL", "METHOD"]);
  });
});

describe("valueMapChanges", () => {
  it("reports additions and removals per outcome", () => {
    expect(valueMapChanges({ BLOCKED: ["A", "B"] }, { BLOCKED: ["B", "C"], ALLOWED: ["D"] })).toEqual([
      { outcome: "BLOCKED", added: ["C"], removed: ["A"] },
      { outcome: "ALLOWED", added: ["D"], removed: [] },
    ]);
  });
});

describe("validationState", () => {
  it("is valid only for the version that was validated", () => {
    expect(validationState(profile()).label).toBe("Valid (v4)");
    expect(validationState(profile({ profile_version: 5 })).label).toBe("Version 5 not validated");
  });
  it("fails closed when VALID carries no validated version", () => {
    const s = validationState(profile({ validated_profile_version: null }));
    expect(s.kind).toBe("unvalidated");
  });
  it("reads UNVALIDATED as a version not validated", () => {
    const s = validationState(profile({ validation_status: "UNVALIDATED", validated_profile_version: null, profile_version: 2 }));
    expect(s).toMatchObject({ kind: "unvalidated", label: "Version 2 not validated", tone: "warn" });
  });
  it("shows a pending validation first", () => {
    const s = validationState(profile({ validation_pending: true, profile_version: 5 }));
    expect(s).toMatchObject({ kind: "pending", label: "Validating v5…" });
  });
  it("names why a version is invalid, in the analyst's words", () => {
    const s = validationState(
      {
        ...profile({ validation_status: "INVALID" }),
        fields: [
          { field: "URL", property_name: "WAF_URI", status: "PROPERTY_REJECTED" },
          { field: "METHOD", property_name: "HTTP_Method", status: "AVAILABLE" },
        ],
      },
      SCHEMA,
    );
    expect(s).toMatchObject({ kind: "invalid", label: "Invalid: URL path property does not exist", tone: "crit" });
  });
  it("falls back to the field key when no schema is at hand", () => {
    expect(
      validationReason([{ field: "URL", property_name: "x", status: "UNPOPULATED" }], null, false),
    ).toBe("URL property carries no values");
  });
  it("does not blame the field for an inconclusive result", () => {
    const s = validationState(
      {
        ...profile({ validation_status: "INCONCLUSIVE" }),
        fields: [{ field: "URL", property_name: "URL", status: "NO_EVENTS" }],
      },
      SCHEMA,
    );
    expect(s).toMatchObject({ kind: "inconclusive", label: "Inconclusive: no events in the window", tone: "warn" });
  });
});

describe("collectionState", () => {
  it("says collecting only when eligible and enabled", () => {
    expect(collectionState(profile())).toEqual({ collecting: true, label: "Collecting" });
  });
  it("says paused, with the reason", () => {
    expect(
      collectionState(profile({ collection_eligible: false, ineligible_reason: "version 5 is not validated" })).label,
    ).toBe("Paused — version 5 is not validated");
  });
  it("never reads eligible-but-disabled as collecting", () => {
    expect(collectionState(profile({ enabled: false })).collecting).toBe(false);
  });
});

describe("sortByAttention", () => {
  const rows = [
    status({ log_source_name: "b-ok" }),
    status({ log_source_name: "z-invalid", validation_status: "INVALID", collection_eligible: false }),
    status({ log_source_name: "a-ok" }),
    status({ log_source_name: "m-pending", validation_pending: true, profile_version: 5, validation_status: "UNVALIDATED", validated_profile_version: 4, collection_eligible: false }),
    status({ log_source_name: "k-unvalidated", profile_version: 3, validation_status: "UNVALIDATED", validated_profile_version: null, collection_eligible: false }),
    status({ log_source_name: "j-inconclusive", validation_status: "INCONCLUSIVE", collection_eligible: false }),
    status({ log_source_name: "c-disabled", enabled: false, collection_eligible: false, ineligible_reason: "disabled" }),
  ];
  it("puts what needs attention first, then orders by name", () => {
    expect(sortByAttention(rows).map((r) => r.log_source_name)).toEqual([
      "z-invalid",
      "j-inconclusive",
      "k-unvalidated",
      "m-pending",
      "c-disabled",
      "a-ok",
      "b-ok",
    ]);
  });
  it("does not mutate its input", () => {
    const copy = [...rows];
    sortByAttention(rows);
    expect(rows).toEqual(copy);
  });
  it("ranks a healthy collecting source last", () => {
    expect(attentionRank(status())).toBe(5);
  });
});

describe("enableBlockReason", () => {
  it("allows enabling a validated current version", () => {
    expect(enableBlockReason(profile({ enabled: false }))).toBeNull();
  });
  it("explains each way it is not allowed", () => {
    expect(enableBlockReason(profile({ profile_version: 5, validation_status: "UNVALIDATED", validated_profile_version: null }))).toMatch(/version 5 has not been validated/i);
    expect(enableBlockReason(profile({ validation_pending: true }))).toMatch(/still being validated/i);
    expect(enableBlockReason(profile({ validation_status: "INVALID" }))).toMatch(/invalid/i);
    expect(enableBlockReason(profile({ validation_status: "INCONCLUSIVE" }))).toMatch(/inconclusive/i);
  });
});

describe("fieldStatusInfo", () => {
  const ctx = { populatedRatio: 0.62, plausibleRatio: null, requiredCoverage: 0.9, usedByCollection: true };
  it("words low coverage with both shares", () => {
    expect(fieldStatusInfo("INSUFFICIENT_COVERAGE", ctx).sentence).toBe(
      "Only 62.0\u00A0% of events carry it; collection needs 90.0\u00A0%.",
    );
  });
  it("does not invent a share when coverage was not measured", () => {
    const s = fieldStatusInfo("INSUFFICIENT_COVERAGE", { ...ctx, populatedRatio: null }).sentence;
    expect(s).not.toMatch(/only/i);
    expect(s).toMatch(/too few events/i);
  });
  it("words NO_EVENTS and QUERY_FAILED as saying nothing about the field", () => {
    for (const st of ["NO_EVENTS", "QUERY_FAILED"]) {
      const i = fieldStatusInfo(st, ctx);
      expect(i.silent).toBe(true);
      expect(i.tone).toBe("neutral");
      expect(i.sentence).toMatch(/says nothing about the field/);
    }
  });
  it("words the rejected and unpopulated statuses", () => {
    expect(fieldStatusInfo("PROPERTY_REJECTED", ctx).sentence).toBe("QRadar does not know a property with this name.");
    expect(fieldStatusInfo("UNPOPULATED", ctx).sentence).toBe("The property exists but no event in the window carried it.");
  });
  it("is a warning, not a failure, for a field collection does not need", () => {
    expect(fieldStatusInfo("UNPOPULATED", { ...ctx, usedByCollection: false }).tone).toBe("warn");
  });
  it("shows an unknown status as recorded", () => {
    expect(fieldStatusInfo("SOMETHING_NEW", ctx).label).toBe("SOMETHING_NEW");
  });
});

describe("catalogNote", () => {
  const c = catalog();
  it("warns about a name the console does not list", () => {
    expect(catalogNote("Nope", c, "Generic WAF DSM")).toMatchObject({ kind: "not-listed" });
    expect(catalogNote("Nope", c, null)?.text).toBe(
      "Not in this console’s property list. Validation will check it.",
    );
  });
  it("warns about a property listed only for another type, naming the type", () => {
    expect(catalogNote("WAF_URL", c, "Generic WAF DSM")?.text).toBe(
      "This property has no parsing expression for this source’s log-source type (Generic WAF DSM).",
    );
  });
  it("says nothing for a property that applies, an unknown applicability or an empty name", () => {
    expect(catalogNote("URL", c, "x")).toBeNull();
    expect(catalogNote("", c, "x")).toBeNull();
    const unknown = catalog({ properties: [{ name: "P", property_type: null, applies_to_source_type: null, name_allowed: true }] });
    expect(catalogNote("P", unknown, null)).toBeNull();
  });
  it("says nothing while the catalog is unavailable", () => {
    expect(catalogNote("Nope", catalog({ status: "UNAVAILABLE", properties: [] }), "x")).toBeNull();
    expect(catalogNote("Nope", null, "x")).toBeNull();
  });
});

describe("conflictInfo", () => {
  it("reads the structured 409 detail", () => {
    const err = new ApiError(409, "Conflict", {
      message: "profile is at version 5", current_version: 5, updated_by: "j.ito", pending_since: null,
    });
    expect(conflictInfo(err)).toEqual({ version: 5, by: "j.ito", message: "profile is at version 5" });
  });
  it("leaves unknowns null and does not parse sentences", () => {
    const none = { version: null, by: null, message: null };
    expect(conflictInfo(new ApiError(409, "Profile is at version 5, changed by j.ito."))).toEqual(none);
    expect(conflictInfo(new Error("x"))).toEqual(none);
    expect(conflictInfo(new ApiError(409, "x", { current_version: null }))).toEqual(none);
  });
  it("shows the server's message and says the edits are kept", () => {
    expect(conflictMessage({ version: 5, by: "j.ito", message: "Mapping is at version 5." })).toBe(
      "Mapping is at version 5. Your edits are kept below; reload to see theirs.",
    );
  });
  it("words a conflict found by polling from version and author", () => {
    expect(conflictMessage({ version: 5, by: "j.ito", message: null })).toBe(
      "This mapping changed to version 5 while you were editing (by j.ito). Your edits are kept below; reload to see theirs.",
    );
    expect(conflictMessage({ version: null, by: null, message: null })).toMatch(/a newer version/);
  });
});

describe("serverMessage", () => {
  it("prefers detail.message, else the plain detail", () => {
    expect(serverMessage(new ApiError(422, "x", { message: "bad name" }))).toBe("bad name");
    expect(serverMessage(new ApiError(409, "plain"))).toBe("plain");
    expect(serverMessage(new Error("x"))).toBe("");
  });
});

describe("reasonText", () => {
  it("renders a known field and status in the table's words", () => {
    expect(reasonText("URL: PROPERTY_REJECTED (QRadar rejected the property WAF_URI)", SCHEMA, 0.9)).toBe(
      "URL path: QRadar does not know a property with this name.",
    );
  });
  it("uses the measured share for low coverage", () => {
    expect(reasonText("METHOD: INSUFFICIENT_COVERAGE (62 of 100)", SCHEMA, 0.9, () => 0.62)).toBe(
      "HTTP method: Only 62.0\u00A0% of events carry it; collection needs 90.0\u00A0%.",
    );
  });
  it("shows anything else verbatim", () => {
    expect(reasonText("the log source does not exist in QRadar", SCHEMA, 0.9)).toBe("the log source does not exist in QRadar");
    expect(reasonText("NOPE: PROPERTY_REJECTED (x)", SCHEMA, 0.9)).toBe("NOPE: PROPERTY_REJECTED (x)");
    expect(reasonText("URL: WEIRD (x)", SCHEMA, 0.9)).toBe("URL: WEIRD (x)");
  });
});

describe("action value map", () => {
  const outcomes = ["BLOCKED", "ALLOWED"];
  it("finds a value's assignment", () => {
    const map = { BLOCKED: ["A"], ALLOWED: ["B"] };
    expect(assignmentOf(map, outcomes, "A")).toBe("BLOCKED");
    expect(assignmentOf(map, outcomes, "B")).toBe("ALLOWED");
    expect(assignmentOf(map, outcomes, "C")).toBeNull();
    expect(assignmentOf({ BLOCKED: ["A"], ALLOWED: ["A"] }, outcomes, "A")).toBe("BOTH");
  });
  it("moves a value between outcomes and to not mapped", () => {
    const map = { BLOCKED: ["A"], ALLOWED: [] };
    expect(assignValue(map, outcomes, "A", "ALLOWED")).toEqual({ BLOCKED: [], ALLOWED: ["A"] });
    expect(assignValue(map, outcomes, "A", null)).toEqual({ BLOCKED: [], ALLOWED: [] });
    expect(map).toEqual({ BLOCKED: ["A"], ALLOWED: [] });
  });
  it("lists discovered values, then mapped values the validation did not see", () => {
    const rows = valueRows([{ value: "A", events: 5 }], { BLOCKED: ["A", "Z"], ALLOWED: [] }, outcomes);
    expect(rows).toEqual([
      { value: "A", events: 5, seen: true },
      { value: "Z", events: null, seen: false },
    ]);
  });
});

describe("history", () => {
  it("reads the changed fields from the audit detail", () => {
    const entry = {
      at: "2026-10-12T22:58:00Z", actor: "a", action: "waf.profile.mapping_changed", profile_version: 2,
      mapping_before: { URL: "request_uri", METHOD: "M" }, mapping_after: { URL: "URL", METHOD: "M" },
      outcome: null, reasons: null,
    };
    expect(historyChanges(SCHEMA, entry)).toEqual([
      { key: "URL", label: "URL path", from: "request_uri", to: "URL" },
    ]);
  });
  it("reports nothing for an entry with no mapping detail", () => {
    expect(
      historyChanges(SCHEMA, { at: "x", actor: "a", action: "waf.profile.enabled", profile_version: 1, mapping_before: null, mapping_after: null, outcome: null, reasons: null }),
    ).toEqual([]);
  });
});

describe("prefill", () => {
  const valid = (id: string, at: string, over = {}) =>
    status({ log_source_id: id, validated_at: at, ...over });
  it("takes the most recently validated VALID profile on the same instance", () => {
    const src = pickPrefillSource(
      [valid("a", "2026-10-01T00:00:00Z"), valid("b", "2026-10-09T00:00:00Z"), valid("c", "2026-10-05T00:00:00Z")],
      "inst-1",
      "self",
    );
    expect(src?.log_source_id).toBe("b");
  });
  it("skips other instances, itself, and anything not VALID for its current version", () => {
    expect(
      pickPrefillSource(
        [
          valid("o", "2026-10-09T00:00:00Z", { instance_id: "inst-2" }),
          valid("self", "2026-10-09T00:00:00Z"),
          valid("inv", "2026-10-09T00:00:00Z", { validation_status: "INVALID" }),
          valid("stale", "2026-10-09T00:00:00Z", { profile_version: 5 }),
          valid("never", "2026-10-09T00:00:00Z", { validated_at: null }),
        ],
        "inst-1",
        "self",
      ),
    ).toBeNull();
  });
  it("seeds nothing when the instance is unknown", () => {
    expect(pickPrefillSource([valid("a", "2026-10-01T00:00:00Z")], null, "self")).toBeNull();
  });
  it("resolves the instance from the log source, else from a single instance", () => {
    expect(resolveInstanceId("x", [])).toBe("x");
    expect(resolveInstanceId(null, [{ instance_id: "i" }, { instance_id: "i" }])).toBe("i");
    expect(resolveInstanceId(null, [{ instance_id: "i" }, { instance_id: "j" }])).toBeNull();
    expect(resolveInstanceId(undefined, [])).toBeNull();
  });
});

describe("fieldForMessage", () => {
  const d = draftFromProfile(SCHEMA, profile());
  it("finds the one field whose property the message names", () => {
    expect(fieldForMessage(SCHEMA, d, "Property name 'HTTP_Method' is not allowed")).toBe("METHOD");
  });
  it("leaves an ambiguous or unrelated message to the form", () => {
    expect(fieldForMessage(SCHEMA, d, "action map needs an action property")).toBeNull();
    expect(fieldForMessage(SCHEMA, d, "URL and HTTP_Method clash")).toBeNull();
  });
});
