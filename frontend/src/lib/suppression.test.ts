import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  DAYS,
  alertFamily,
  alertTarget,
  allowsWholeSource,
  canSuppress,
  fieldOf,
  permissionOf,
  spanDays,
  suppressFailure,
  untilEnd,
} from "./suppression";
import { anomalyTarget } from "./suppressTarget";

const say = (key: string, values?: Record<string, unknown>) =>
  `${key}${values ? JSON.stringify(values) : ""}`;

describe("durations", () => {
  it("offer 1, 7 and 30 days and nothing else", () => {
    expect(DAYS).toEqual([1, 7, 30]);
  });
});

describe("which alerts and anomalies have a condition to quiet", () => {
  it("maps the five anomaly producers to a family and rejects every other source type", () => {
    expect(alertFamily({ source_type: "log_source" })).toBe("log_source");
    expect(alertFamily({ source_type: "waf_endpoint" })).toBe("waf");
    expect(alertFamily({ source_type: "ips_entity" })).toBe("ips");
    expect(alertFamily({ source_type: "ips_source" })).toBe("ips");
    expect(alertFamily({ source_type: "pw_value" })).toBe("property_watch");
    expect(alertFamily({ source_type: "scheduled_search" })).toBeNull();
    expect(alertFamily({ source_type: "constructor" })).toBeNull();
  });

  it("needs an anomaly id to read the condition from", () => {
    expect(alertTarget({ id: "a", source_type: "log_source", source_anomaly_ids: ["x", "y"] })).toEqual({
      via: "alert",
      alertId: "a",
      family: "log_source",
      anomalyId: "x",
      alertDetector: null,
    });
    expect(
      alertTarget({
        id: "a",
        source_type: "log_source",
        source_anomaly_ids: ["x"],
        title_params: { anomaly_type: "VOLUME_DROP", log_source: "Edge" },
      }),
    ).toMatchObject({ alertDetector: "VOLUME_DROP" });
    expect(
      alertTarget({ id: "a", source_type: "waf_endpoint", source_anomaly_ids: ["x"], title_params: { detector: "WAF_BLOCK_RATIO_SPIKE" } }),
    ).toMatchObject({ alertDetector: "WAF_BLOCK_RATIO_SPIKE" });
    expect(alertTarget({ id: "a", source_type: "log_source", source_anomaly_ids: [] })).toBeNull();
    expect(alertTarget({ id: "a", source_type: "catchall_sender", source_anomaly_ids: ["x"] })).toBeNull();
  });

  it("takes the family of an anomaly from its subject kind, a missing kind being a log source", () => {
    expect(anomalyTarget({ id: "n", subject_kind: "WAF_ENDPOINT" })).toEqual({
      via: "anomaly",
      family: "waf",
      anomalyId: "n",
    });
    expect(anomalyTarget({ id: "n" })?.family).toBe("log_source");
    expect(anomalyTarget({ id: "n", subject_kind: "NEW_KIND" as never })).toBeNull();
  });

  it("allows the whole source for the three source-anchored families only", () => {
    expect(allowsWholeSource("log_source")).toBe(true);
    expect(allowsWholeSource("waf")).toBe(true);
    expect(allowsWholeSource("ips")).toBe(true);
    expect(allowsWholeSource("property_watch")).toBe(false);
  });
});

describe("permission", () => {
  it("holds the exact grant or the administrator wildcard", () => {
    expect(canSuppress(["alert:suppress"])).toBe(true);
    expect(canSuppress(["admin:*"])).toBe(true);
    expect(canSuppress(["read:alerts", "anomaly:triage"])).toBe(false);
  });
});

describe("time", () => {
  const now = Date.parse("2026-11-07T07:00:00Z");
  it("says how far ahead an end lies, with the largest whole unit", () => {
    expect(untilEnd("2026-11-13T07:00:00Z", now)).toEqual({ unit: "days", count: 6 });
    expect(untilEnd("2026-11-07T10:30:00Z", now)).toEqual({ unit: "hours", count: 3 });
    expect(untilEnd("2026-11-07T07:12:00Z", now)).toEqual({ unit: "minutes", count: 12 });
    expect(untilEnd("2026-11-07T07:00:30Z", now)).toEqual({ unit: "underMinute" });
  });
  it("says an end in the past is past, and a missing or unreadable end is unknown", () => {
    expect(untilEnd("2026-11-07T07:00:00Z", now)).toEqual({ unit: "past" });
    expect(untilEnd("2026-11-01T00:00:00Z", now)).toEqual({ unit: "past" });
    expect(untilEnd(null, now)).toBeNull();
    expect(untilEnd("not a date", now)).toBeNull();
  });
  it("spans whole days, and nothing without both ends", () => {
    expect(spanDays({ starts_at: "2026-11-06T07:00:00Z", ends_at: "2026-11-13T07:00:00Z" })).toBe(7);
    expect(spanDays({ starts_at: "2026-11-06T07:00:00Z", ends_at: "2026-11-06T07:10:00Z" })).toBe(1);
    expect(spanDays({ starts_at: null, ends_at: "2026-11-13T07:00:00Z" })).toBeNull();
    expect(spanDays({ starts_at: "2026-11-13T07:00:00Z", ends_at: "2026-11-06T07:00:00Z" })).toBeNull();
  });
});

describe("failures", () => {
  it("finds the field a schema 422 names from its location", () => {
    const err = new ApiError(422, "String should have at least 3 characters", [
      { loc: ["body", "reason"], msg: "String should have at least 3 characters" },
    ]);
    expect(fieldOf(err)).toBe("reason");
    expect(fieldOf(new ApiError(422, "x", [{ loc: ["body", "unknown"], msg: "x" }]))).toBeNull();
    expect(fieldOf(new ApiError(422, "property watch has no whole-source scope", "property watch has no whole-source scope"))).toBeNull();
  });

  it("names the permission a 403 sentence carries", () => {
    expect(permissionOf(new ApiError(403, "missing required permission: alert:suppress"))).toBe("alert:suppress");
    expect(permissionOf(new ApiError(403, "forbidden"))).toBeNull();
  });

  it("maps 404 to gone, 403 to forbidden naming the permission, 422 to the server's sentence", () => {
    expect(suppressFailure(new ApiError(404, "gone"), say as never)).toMatchObject({ gone: true, kind: "error" });
    expect(
      suppressFailure(new ApiError(403, "missing required permission: read:anomalies"), say as never),
    ).toMatchObject({ kind: "forbidden", text: 'suppression.failure.forbidden{"permission":"read:anomalies"}' });
    expect(suppressFailure(new ApiError(403, "no"), say as never).text).toContain("alert:suppress");
    const f = suppressFailure(
      new ApiError(422, "an extend past 30 days", "an extend past 30 days"),
      say as never,
    );
    expect(f).toMatchObject({ kind: "error", text: "an extend past 30 days", field: null, gone: false });
  });

  it("reads a schema 422's sentences from its detail, not the status text", () => {
    const f = suppressFailure(
      new ApiError(422, "Unprocessable Content", [
        { loc: ["body", "reason"], msg: "String should have at least 3 characters" },
        { loc: ["body"], msg: "Value error, a KNOWN_CHANGE suppression requires a reference" },
      ]),
      say as never,
    );
    expect(f.text).toBe(
      "String should have at least 3 characters; a KNOWN_CHANGE suppression requires a reference",
    );
    expect(f.field).toBe("reason");
  });

  it("says nothing was changed for any other failure", () => {
    const f = suppressFailure(new ApiError(500, "boom"), say as never);
    expect(f.text.startsWith("suppression.failure.notSaved")).toBe(true);
    expect(f.gone).toBe(false);
  });
});
