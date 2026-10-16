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
    expect(statusLabel(null)).toBe("NEVER_EVALUATED");
    expect(statusReason(null, null)).toBe("No bucket has been judged yet.");
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
