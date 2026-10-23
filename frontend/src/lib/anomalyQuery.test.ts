// Query-string parsing for the anomaly list. These parameters arrive from a
// URL, so the tests are as much about what is rejected as about what is kept.

import { describe, expect, it } from "vitest";

import {
  MAX_RANGE_DAYS,
  paginationParams,
  parseAnomalyQuery,
  parseSubjectQuery,
} from "./anomalyQuery";

const UUID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301";

describe("filter parsing", () => {
  it("keeps recognized enum values", () => {
    const q = parseAnomalyQuery({
      anomaly_type: "VOLUME_SPIKE",
      state: "OPEN",
      severity: "HIGH",
      evidence_status: "PARTIAL",
    });
    expect(q.anomaly_type).toBe("VOLUME_SPIKE");
    expect(q.state).toBe("OPEN");
    expect(q.severity).toBe("HIGH");
    expect(q.evidence_status).toBe("PARTIAL");
  });

  it("drops an unrecognized enum value rather than forwarding it", () => {
    // A crafted value must not reach the upstream request or be reflected back
    // into the rendered filter form.
    const q = parseAnomalyQuery({
      anomaly_type: "<script>alert(1)</script>",
      state: "DEFINITELY_NOT_A_STATE",
      severity: "'; DROP TABLE--",
      evidence_status: "../../etc/passwd",
    });
    expect(q.anomaly_type).toBe("");
    expect(q.state).toBe("");
    expect(q.severity).toBe("");
    expect(q.evidence_status).toBe("");
  });

  it("accepts a well-formed id and rejects anything else", () => {
    expect(parseAnomalyQuery({ log_source_id: UUID }).log_source_id).toBe(UUID);
    expect(parseAnomalyQuery({ log_source_id: "not-an-id" }).log_source_id).toBe("");
    expect(parseAnomalyQuery({ instance_id: UUID }).instance_id).toBe(UUID);
    expect(parseAnomalyQuery({ instance_id: "1 OR 1=1" }).instance_id).toBe("");
  });

  it("defaults every filter to empty", () => {
    const q = parseAnomalyQuery({});
    expect(q.active_only).toBe(false);
    expect(q.state).toBe("");
    expect(q.since).toBe("");
    expect(q.offset).toBe(0);
    expect(q.rangeNote).toBeNull();
  });

  it("accepts only an explicit true active-only flag", () => {
    expect(parseAnomalyQuery({ active_only: "true" }).active_only).toBe(true);
    expect(parseAnomalyQuery({ active_only: "false" }).active_only).toBe(false);
    expect(parseAnomalyQuery({ active_only: "1" }).active_only).toBe(false);
  });
});

describe("offset parsing", () => {
  it("reads a valid offset", () => {
    expect(parseAnomalyQuery({ offset: "50" }).offset).toBe(50);
  });

  it("falls back to the first page rather than forwarding nonsense", () => {
    expect(parseAnomalyQuery({ offset: "-25" }).offset).toBe(0);
    expect(parseAnomalyQuery({ offset: "abc" }).offset).toBe(0);
    expect(parseAnomalyQuery({ offset: "Infinity" }).offset).toBe(0);
  });

  it("floors a fractional offset", () => {
    expect(parseAnomalyQuery({ offset: "10.9" }).offset).toBe(10);
  });
});

describe("time range", () => {
  it("normalizes a local datetime to an instant", () => {
    const q = parseAnomalyQuery({ since: "2026-07-20T10:00" });
    expect(q.since).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(Number.isNaN(Date.parse(q.since))).toBe(false);
  });

  it("drops an unparseable timestamp", () => {
    expect(parseAnomalyQuery({ since: "yesterday" }).since).toBe("");
  });

  it("swaps an inverted range and says so", () => {
    const q = parseAnomalyQuery({
      since: "2026-07-21T10:00",
      until: "2026-07-20T10:00",
    });
    expect(Date.parse(q.since)).toBeLessThan(Date.parse(q.until));
    expect(q.rangeNote).toMatch(/inverted/i);
  });

  it("clamps a range wider than the backend allows", () => {
    // Sending it unclamped would only earn a 422; the operator gets the most
    // recent allowed window instead, and is told it was narrowed.
    const q = parseAnomalyQuery({
      since: "2000-01-01T00:00",
      until: "2026-07-20T10:00",
    });
    const span = Date.parse(q.until) - Date.parse(q.since);
    expect(span).toBeLessThanOrEqual(MAX_RANGE_DAYS * 86400_000);
    expect(q.rangeNote).toMatch(/clamped/i);
  });

  it("leaves an in-bounds range untouched", () => {
    const q = parseAnomalyQuery({
      since: "2026-07-19T10:00",
      until: "2026-07-20T10:00",
    });
    expect(q.rangeNote).toBeNull();
  });
});

describe("paginationParams", () => {
  it("carries every active filter into the page links", () => {
    const q = parseAnomalyQuery({
      log_source_id: UUID,
      state: "OPEN",
      severity: "HIGH",
      evidence_status: "FAILED",
      anomaly_type: "VOLUME_DROP",
      active_only: "true",
      offset: "25",
    });
    expect(paginationParams(q)).toMatchObject({
      log_source_id: UUID,
      state: "OPEN",
      severity: "HIGH",
      evidence_status: "FAILED",
      anomaly_type: "VOLUME_DROP",
      active_only: "true",
    });
  });

  it("omits empty filters so the URL does not accumulate bare keys", () => {
    const params = paginationParams(parseAnomalyQuery({}));
    expect(Object.values(params).every((v) => v === undefined)).toBe(true);
  });

  it("does not carry the offset: the pagination control owns it", () => {
    const params = paginationParams(parseAnomalyQuery({ offset: "25" }));
    expect("offset" in params).toBe(false);
  });
});

describe("subject parsing", () => {
  it("accepts the two subject kinds and nothing else", () => {
    expect(parseSubjectQuery({ subject_kind: "WAF_ENDPOINT" }).subject_kind).toBe("WAF_ENDPOINT");
    expect(parseSubjectQuery({ subject_kind: "LOG_SOURCE" }).subject_kind).toBe("LOG_SOURCE");
    expect(parseSubjectQuery({ subject_kind: "<script>" }).subject_kind).toBe("");
    expect(parseSubjectQuery({}).subject_kind).toBe("");
  });

  it("accepts a WAF detector type from anomaly_type and drops anything else", () => {
    expect(parseSubjectQuery({ anomaly_type: "WAF_BLOCK_RATIO_SPIKE" }).waf_anomaly_type).toBe(
      "WAF_BLOCK_RATIO_SPIKE",
    );
    // A Phase A type is `parseAnomalyQuery`'s to parse, not this function's.
    expect(parseSubjectQuery({ anomaly_type: "VOLUME_SPIKE" }).waf_anomaly_type).toBe("");
    expect(parseSubjectQuery({ anomaly_type: "WAF_NOPE" }).waf_anomaly_type).toBe("");
  });
});

describe("time filters", () => {
  it("reads a zoneless datetime-local value as UTC, whatever the server timezone", () => {
    // Left bare, Date.parse reads it in the server's zone, so the same URL
    // would filter a different window on a differently configured host.
    expect(parseAnomalyQuery({ since: "2026-10-12T09:15" }).since).toBe(
      "2026-10-12T09:15:00.000Z",
    );
  });

  it("honours an explicit offset rather than overriding it", () => {
    expect(parseAnomalyQuery({ since: "2026-10-12T11:15:00+02:00" }).since).toBe(
      "2026-10-12T09:15:00.000Z",
    );
    expect(parseAnomalyQuery({ since: "2026-10-12T09:15:00Z" }).since).toBe(
      "2026-10-12T09:15:00.000Z",
    );
  });

  it("still rejects a value that is not a timestamp", () => {
    expect(parseAnomalyQuery({ since: "yesterday" }).since).toBe("");
  });
});

