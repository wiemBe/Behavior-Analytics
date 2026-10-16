import { describe, expect, it } from "vitest";

import {
  classifySaveError,
  conditionText,
  customRuleAccessFrom,
  draftReason,
  editorSummary,
  emptyDraft,
  statusLabel,
  statusReason,
  statusTone,
  subjectKindInSentence,
} from "./authoring";
import { catalog, reader, rule, writer } from "./fixtures";
import { ApiError } from "@/lib/api";

describe("custom-rule authoring semantics", () => {
  it("recognises only the write capability and admin wildcard", () => {
    expect(customRuleAccessFrom(reader)).toBe("reader");
    expect(customRuleAccessFrom(writer)).toBe("writer");
    expect(customRuleAccessFrom({ ...reader, permissions: ["admin:*"] })).toBe("writer");
  });

  it("renders the condition and hysteresis with units", () => {
    expect(conditionText(rule())).toBe("average EPS > 10 events/s for 2 buckets");
    expect(editorSummary("GT", 10, "events/s", 2, 2)).toBe(
      "Fires after 2 consecutive buckets above 10 events/s; recovers after 2 buckets at or below.",
    );
  });

  it("treats a never-evaluated rule as its own state", () => {
    expect(statusLabel(null)).toBe("Never evaluated");
    expect(statusReason(null, null)).toBeNull();
  });

  it("names each status in words and lets only OK take the positive tone", () => {
    const words: Record<string, string> = {
      OK: "OK",
      OPEN: "Open",
      RECOVERING: "Recovering",
      RESOLVED: "Resolved",
      NO_VERDICT: "No verdict",
      STALE: "Stale",
      NOT_COLLECTED: "Not collected",
      SUBJECT_RETIRED: "Subject retired",
      DISABLED: "Disabled",
      NOT_ENABLED: "Not enabled",
    };
    for (const [status, label] of Object.entries(words)) {
      expect(statusLabel(status)).toBe(label);
      expect(label).not.toMatch(/_/);
    }
    expect(statusTone("OK")).toBe("ok");
    for (const status of ["NOT_ENABLED", "STALE", "NOT_COLLECTED", "NO_VERDICT", "SUBJECT_RETIRED", "DISABLED", "OPEN", "RECOVERING", "RESOLVED", null]) {
      expect(statusTone(status)).not.toBe("ok");
    }
    expect(statusTone("OPEN")).toBe("crit");
    expect(statusTone("STALE")).toBe("warn");
    expect(statusTone("RECOVERING")).toBe("warn");
    expect(statusTone("NOT_ENABLED")).toBe("");
    expect(statusTone("NOT_COLLECTED")).toBe("");
    expect(statusTone("NO_VERDICT")).toBe("");
  });

  it("shows an engine reason only when one was recorded", () => {
    expect(statusReason("OPEN", null)).toBeNull();
    expect(statusReason("OK", null)).toBeNull();
    expect(statusReason("RECOVERING", "  ")).toBeNull();
    expect(statusReason("NOT_ENABLED", " CUSTOM_RULES_ENABLED is off ")).toBe("CUSTOM_RULES_ENABLED is off");
    expect(statusReason("DISABLED", "the rule is disabled")).toBe("the rule is disabled");
  });

  it("keeps acronyms in kind labels used in a sentence", () => {
    expect(subjectKindInSentence("WAF_ENDPOINT")).toBe("WAF endpoint");
    expect(subjectKindInSentence("IPS_ENTITY")).toBe("IPS entity");
    expect(subjectKindInSentence("IPS_SOURCE")).toBe("IPS source");
    expect(subjectKindInSentence("LOG_SOURCE")).toBe("log source");
    expect(subjectKindInSentence("FLEET_CLASS")).toBe("fleet class");
    const draft = emptyDraft(catalog());
    expect(draftReason({ ...draft, name: "Named" }, catalog().metrics[0])).toBe("Choose a log source.");
    const waf = catalog().metrics.find((metric) => metric.metric === "REQUEST_COUNT")!;
    expect(draftReason({ ...draft, name: "Named", subjectKind: "WAF_ENDPOINT", metric: "REQUEST_COUNT" }, waf)).toBe(
      "Choose a WAF endpoint.",
    );
  });

  it("guards ratio, subject, sustain and recovery inputs", () => {
    const c = catalog();
    const draft = emptyDraft(c);
    expect(draftReason(draft, c.metrics[0])).toMatch(/Name is required/);
    const ratio = c.metrics.find((metric) => metric.metric === "BLOCK_RATIO")!;
    expect(
      draftReason(
        {
          ...draft,
          name: "Ratio",
          subjectKind: "WAF_ENDPOINT",
          wafEndpointId: "endpoint-1",
          metric: "BLOCK_RATIO",
          threshold: "1",
        },
        ratio,
      ),
    ).toMatch(/greater than zero and less than one/);
  });
});

describe("custom-rule API refusals", () => {
  it("preserves every 422 reason exactly", () => {
    expect(
      classifySaveError(
        new ApiError(422, "Unprocessable Entity", {
          message: "invalid",
          reasons: ["first reason", "second reason"],
        }),
      ),
    ).toEqual({ kind: "reasons", reasons: ["first reason", "second reason"] });
  });

  it("distinguishes the enabled cap from a version conflict", () => {
    expect(classifySaveError(new ApiError(409, "cap", "at most 100 rules may be enabled at once"))).toEqual({
      kind: "cap",
      detail: "at most 100 rules may be enabled at once",
    });
    expect(
      classifySaveError(
        new ApiError(409, "Conflict", {
          message: "rule version conflict",
          current_version: "2026-10-16T08:00:00+00:00",
          updated_by: "grace",
        }),
      ),
    ).toEqual({
      kind: "version",
      currentVersion: "2026-10-16T08:00:00+00:00",
      updatedBy: "grace",
    });
  });
});
