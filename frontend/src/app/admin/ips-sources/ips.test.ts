import { describe, expect, it } from "vitest";

import {
  draftFromDefaults,
  draftFromIpsProfile,
  draftFromPreset,
  draftToIpsInput,
  genericNameNotes,
  historyForTable,
  ipsDraftChanges,
  ipsFieldStatusInfo,
  ipsReasonText,
  lastValidationWasBusy,
} from "./ips";
import { HISTORY, IPS_SCHEMA, fieldReport, profile, report } from "./fixtures";

describe("the IPS draft", () => {
  it("starts from the schema defaults, ACTION unmapped", () => {
    const d = draftFromDefaults(IPS_SCHEMA);
    expect(d.mapping.SOURCE_IP).toBe("sourceip");
    expect(d.kinds.SOURCE_IP).toBe("NORMALIZED");
    expect(d.mapping.ACTION).toBe("");
    expect(d.preset).toBeNull();
  });

  it("round-trips a profile into a request with kinds and nulls", () => {
    const p = profile({
      mapping: { ...profile().mapping, ACTION: { kind: "CUSTOM_PROPERTY", name: "PAN_Action" }, PROTOCOL: null },
      action_value_map: { BLOCKED: ["drop"], ALLOWED: [] },
    });
    const body = draftToIpsInput(IPS_SCHEMA, draftFromIpsProfile(IPS_SCHEMA, p), 2);
    expect(body.mapping.ACTION).toEqual({ kind: "CUSTOM_PROPERTY", name: "PAN_Action" });
    expect(body.mapping.PROTOCOL).toBeNull();
    expect(body.action_value_map).toEqual({ BLOCKED: ["drop"], ALLOWED: [] });
    expect(body.preset).toBe("CISCO_FTD");
    expect(body.expected_profile_version).toBe(2);
  });

  it("keeps the typed label when a preset is chosen", () => {
    const d = draftFromPreset(IPS_SCHEMA, "PALO_ALTO_THREAT", "my label");
    expect(d.productLabel).toBe("my label");
    expect(d.preset).toBe("PALO_ALTO_THREAT");
  });

  it("detects a kind change of an unchanged name, and ignores the kind of an unmapped field", () => {
    const base = draftFromDefaults(IPS_SCHEMA);
    const kindOnly = { ...base, kinds: { ...base.kinds, SEVERITY: "CUSTOM_PROPERTY" as const } };
    expect(ipsDraftChanges(IPS_SCHEMA, base, kindOnly).fields).toHaveLength(1);
    const unmapped = { ...base, kinds: { ...base.kinds, ACTION: "CUSTOM_PROPERTY" as const } };
    expect(ipsDraftChanges(IPS_SCHEMA, base, unmapped).total).toBe(0);
  });
});

describe("validation words", () => {
  it("words FIELD_REJECTED, which the WAF table does not know", () => {
    expect(ipsFieldStatusInfo("FIELD_REJECTED", { populatedRatio: null, plausibleRatio: null, requiredCoverage: null, usedByCollection: true }).sentence).toBe(
      "QRadar does not accept this field as named.",
    );
  });

  it("words reasons and keeps an unknown one as recorded, capitalised", () => {
    expect(ipsReasonText("SIGNATURE: FIELD_REJECTED (x)", IPS_SCHEMA, () => null)).toBe(
      "Signature: QRadar does not accept this field as named.",
    );
    expect(ipsReasonText("the log source does not exist in QRadar", IPS_SCHEMA, () => null)).toBe(
      "The log source does not exist in QRadar",
    );
  });

  it("derives the generic-name warning from the measured share only", () => {
    expect(genericNameNotes(report())).toEqual([]);
    const withGeneric = report({
      fields: [fieldReport({ field: "SIGNATURE", generic_name_ratio: 0.124, generic_name_events: 1240 })],
    });
    expect(genericNameNotes(withGeneric)[0].text).toMatch(/^12\.4\s% of events carry a generic/);
    const zero = report({ fields: [fieldReport({ field: "SIGNATURE", generic_name_ratio: 0, generic_name_events: 0 })] });
    expect(genericNameNotes(zero)).toEqual([]);
  });

  it("keeps a report warning this build has no sentence for", () => {
    const notes = genericNameNotes(report({ warnings: ["something new happened"] }));
    expect(notes[0].text).toBe("Something new happened");
  });
});

describe("history", () => {
  it("flattens references and labels custom properties", () => {
    const rows = historyForTable(HISTORY);
    expect(rows[1].mapping_before?.SIGNATURE).toBe("PAN_ThreatID (custom property)");
    expect(rows[1].mapping_after?.SIGNATURE).toBe("qid");
    expect(rows[1].mapping_after?.ACTION).toBeNull();
  });

  it("tells a busy validation from a result only while it is the newest event of the version", () => {
    const busy = { ...HISTORY[0], outcome: "SOURCE_BUSY" };
    expect(lastValidationWasBusy([busy, ...HISTORY], 2)).toBe(true);
    expect(lastValidationWasBusy([busy, ...HISTORY], 3)).toBe(false);
    expect(lastValidationWasBusy([HISTORY[0], busy], 2)).toBe(false);
    expect(lastValidationWasBusy(null, 2)).toBe(false);
    expect(lastValidationWasBusy([], 2)).toBe(false);
  });
});
