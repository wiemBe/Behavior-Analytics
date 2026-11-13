// SOC-13f: which anomalies can carry the training control, where each one posts, and the words
// that depend on the family (a rule never reports "applied").

import { describe, expect, it } from "vitest";

import {
  exclusionAvailability,
  exclusionTargetOf,
  liveBucketWidthFor,
  resultKey,
  stateKey,
  type TargetFields,
} from "@/lib/trainingExclusion";

const waf: TargetFields = {
  subject_kind: "WAF_ENDPOINT",
  anomaly_type: "WAF_REQUEST_RATE_SPIKE",
  waf_endpoint: { id: "ep-1" },
};
const entity: TargetFields = {
  subject_kind: "IPS_ENTITY",
  anomaly_type: "IPS_PORT_RATE_SPIKE",
  ips_entity: { id: "ent-1" },
};
const source: TargetFields = {
  subject_kind: "IPS_SOURCE",
  anomaly_type: "IPS_BLOCK_RATIO_SHIFT",
  log_source_id: "src-1",
};
const pw: TargetFields = {
  subject_kind: "PW_VALUE",
  anomaly_type: "PW_VALUE_DROP",
  pw_value: { value_id: "val-1" },
};
const storm: TargetFields = { subject_kind: "RULE", anomaly_type: "RULE_STORM", rule: { rule_id: "rule-1" } };
const silence: TargetFields = { subject_kind: "RULE", anomaly_type: "RULE_SILENCE", rule: { rule_id: "rule-1" } };

describe("exclusionTargetOf", () => {
  it.each([
    ["Phase A (no subject kind)", { log_source_id: "s" }, "log_source", "source", "log_source", "s", "log_source:volume"],
    ["WAF endpoint", waf, "waf", "waf", "endpoint", "ep-1", "waf:endpoint"],
    ["IPS entity", entity, "ips", "ipsEntity", "entity", "ent-1", "ips:entity"],
    ["IPS source", source, "ips", "ipsSource", "source", "src-1", "ips:source"],
    ["property-watch value", pw, "property_watch", "pwValue", "value", "val-1", "pw:value"],
    ["rule storm", storm, "rule", "ruleStorm", "rule", "rule-1", "rule:storm"],
    ["rule silence", silence, "rule", "ruleSilence", "rule", "rule-1", "rule:silence"],
  ] as const)("%s", (_name, fields, family, variant, subjectKind, subjectId, scopeKey) => {
    expect(exclusionTargetOf(fields as TargetFields)).toEqual({
      family,
      variant,
      subjectKind,
      subjectId,
      scopeKey,
    });
  });

  it.each([
    ["a WAF new endpoint", { ...waf, anomaly_type: "WAF_NEW_ENDPOINT_ACCESS" }],
    ["an IPS new destination port", { ...entity, anomaly_type: "IPS_NEW_DESTINATION_PORT" }],
    ["a new property value", { ...pw, anomaly_type: "PW_NEW_VALUE" }],
    ["a new property combination", { ...pw, anomaly_type: "PW_NEW_COMBINATION" }],
  ])("%s has no band: no target", (_name, fields) => {
    expect(exclusionTargetOf(fields)).toBeNull();
  });

  it.each([
    ["a WAF anomaly without its endpoint", { ...waf, waf_endpoint: null }],
    ["an IPS entity anomaly without its entity", { ...entity, ips_entity: null }],
    ["an IPS source anomaly of another detector", { ...source, anomaly_type: "VOLUME_SPIKE" }],
    ["an IPS source anomaly without its source", { ...source, log_source_id: null }],
    ["a property-watch anomaly whose value was deleted", { ...pw, pw_value: { value_id: null } }],
    ["a rule anomaly of an unknown detector", { ...storm, anomaly_type: "RULE_OTHER" }],
    ["a rule anomaly without its rule", { ...storm, rule: null }],
    ["an unknown subject kind", { subject_kind: "SOMETHING" }],
  ])("%s: no target", (_name, fields) => {
    expect(exclusionTargetOf(fields)).toBeNull();
  });
});

describe("exclusionAvailability", () => {
  it("is available only for a resolved anomaly with a target", () => {
    expect(exclusionAvailability({ ...waf, state: "RESOLVED" })).toBe("available");
    expect(exclusionAvailability({ ...storm, state: "OPEN" })).toBe("active");
    expect(exclusionAvailability({ ...storm, state: "SUPPRESSED" })).toBe("not-resolved");
  });

  it("is unsupported when there is no target, whatever the state", () => {
    expect(exclusionAvailability({ ...pw, anomaly_type: "PW_NEW_VALUE", state: "RESOLVED" })).toBe("unsupported");
  });
});

describe("the words of a row", () => {
  it("a pending rule row reads 'used from the next evaluation', every other pending row 'rebuild'", () => {
    expect(stateKey({ subject_kind: "rule", state: "PENDING_REBUILD" })).toBe("stateRule.PENDING_REBUILD");
    expect(stateKey({ subject_kind: "endpoint", state: "PENDING_REBUILD" })).toBe("state.PENDING_REBUILD");
    expect(resultKey({ subject_kind: "rule", state: "PENDING_REBUILD" })).toBe("resultRule.PENDING_REBUILD");
    expect(resultKey({ subject_kind: "value", state: "PENDING_REBUILD" })).toBe("result.PENDING_REBUILD");
  });

  it("other states of a rule row keep the shared words", () => {
    expect(stateKey({ subject_kind: "rule", state: "REVOKED" })).toBe("state.REVOKED");
    expect(stateKey({ subject_kind: "rule", state: "OUTSIDE_LOOKBACK" })).toBe("state.OUTSIDE_LOOKBACK");
  });
});

describe("liveBucketWidthFor", () => {
  const detected = { detected_at: "2026-11-04T08:07:00Z" };
  const b = (start: string, seconds: number, origin?: string | null) => ({
    bucket_start: start,
    bucket_seconds: seconds,
    origin,
  });

  it("takes the latest live bucket at or before detection", () => {
    expect(
      liveBucketWidthFor(detected, [
        b("2026-11-04T07:00:00Z", 600, "LIVE"),
        b("2026-11-04T08:05:00Z", 300, "LIVE"),
        b("2026-11-04T09:00:00Z", 900, "LIVE"),
      ]),
    ).toBe(300);
  });

  it("ignores a history bucket: it carries the replay's width, not the live one", () => {
    expect(
      liveBucketWidthFor(detected, [b("2026-11-04T07:00:00Z", 600, "LIVE"), b("2026-11-04T08:05:00Z", 3600, "HISTORY")]),
    ).toBe(600);
  });

  it("ignores a bucket whose origin is unknown, and counts an absent origin as live", () => {
    expect(liveBucketWidthFor(detected, [b("2026-11-04T08:00:00Z", 900, null)])).toBeNull();
    expect(liveBucketWidthFor(detected, [b("2026-11-04T08:00:00Z", 900)])).toBe(900);
  });

  it("is null when the series could not be read or has nothing live before detection", () => {
    expect(liveBucketWidthFor(detected, null)).toBeNull();
    expect(liveBucketWidthFor(detected, [])).toBeNull();
    expect(liveBucketWidthFor(detected, [b("2026-11-04T09:00:00Z", 300, "LIVE")])).toBeNull();
  });
});
