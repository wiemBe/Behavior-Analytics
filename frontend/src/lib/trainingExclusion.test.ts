import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { resolved } from "@/components/baseline/fixtures";
import {
  availability,
  bucketWidthFor,
  canExclude,
  defaultInterval,
  intervalProblem,
  intervalText,
  parseUtcField,
  permissionOf,
  utcFieldText,
} from "./trainingExclusion";

const base = { startMs: Date.UTC(2026, 10, 4, 8, 5), endMs: Date.UTC(2026, 10, 4, 9, 55) };

describe("canExclude", () => {
  it("accepts the exact grant and the administrator wildcard only", () => {
    expect(canExclude(["anomaly:triage"])).toBe(true);
    expect(canExclude(["admin:*"])).toBe(true);
    expect(canExclude(["read:*", "read:anomalies"])).toBe(false);
  });
});

describe("availability", () => {
  it("offers the control for a resolved Phase A anomaly, with or without a subject kind", () => {
    expect(availability({ state: "RESOLVED", subject_kind: "LOG_SOURCE" })).toBe("available");
    expect(availability({ state: "RESOLVED" })).toBe("available");
  });
  it.each(["CANDIDATE", "OPEN", "RECOVERING"] as const)("%s is still active", (state) => {
    expect(availability({ state, subject_kind: "LOG_SOURCE" })).toBe("active");
  });
  it.each(["NORMAL", "SUPPRESSED", "INSUFFICIENT_DATA"] as const)("%s is not resolved", (state) => {
    expect(availability({ state, subject_kind: "LOG_SOURCE" })).toBe("not-resolved");
  });
  it.each(["WAF_ENDPOINT", "IPS_ENTITY", "IPS_SOURCE", "PW_VALUE", "RULE"] as const)(
    "%s is another family",
    (subject_kind) => {
      expect(availability({ state: "RESOLVED", subject_kind })).toBe("unsupported");
    },
  );
});

describe("bucketWidthFor", () => {
  const a = resolved();
  it("takes the latest bucket at or before detection, as the server does", () => {
    expect(
      bucketWidthFor(a, [
        { bucket_start: "2026-11-04T07:50:00Z", bucket_seconds: 60 },
        { bucket_start: "2026-11-04T08:05:00Z", bucket_seconds: 300 },
        { bucket_start: "2026-11-04T08:10:00Z", bucket_seconds: 900 },
      ]),
    ).toBe(300);
  });
  it("falls back to the width the detection recorded", () => {
    const d = resolved({ detection: { bucket_seconds: 600 } as never });
    expect(bucketWidthFor(d, [{ bucket_start: "2026-11-04T09:00:00Z", bucket_seconds: 300 }])).toBe(600);
  });
  it("is null when neither is known, never a guessed width", () => {
    expect(bucketWidthFor(a, [])).toBeNull();
    expect(bucketWidthFor(resolved({ detection: { bucket_seconds: null } as never }), [])).toBeNull();
  });
});

describe("defaultInterval", () => {
  it("aligns outward to the bucket width: start down, end up", () => {
    expect(defaultInterval(resolved(), 300)).toEqual(base);
  });
  it("measures the end as the server does: resolved_at, else anomaly_end, else detected_at", () => {
    const noResolved = resolved({ resolved_at: null });
    expect(defaultInterval(noResolved, 300)?.endMs).toBe(Date.UTC(2026, 10, 4, 9, 45));
    const neither = resolved({ resolved_at: null, anomaly_end: null });
    expect(defaultInterval(neither, 300)?.endMs).toBe(Date.UTC(2026, 10, 4, 8, 10));
  });
  it("never returns an empty interval for an incident on a bucket boundary", () => {
    const edge = resolved({ detected_at: "2026-11-04T08:05:00Z", resolved_at: "2026-11-04T08:05:00Z" });
    expect(defaultInterval(edge, 300)).toEqual({
      startMs: Date.UTC(2026, 10, 4, 8, 5),
      endMs: Date.UTC(2026, 10, 4, 8, 10),
    });
  });
  it("is null for an unreadable time or width", () => {
    expect(defaultInterval(resolved({ detected_at: "nope" }), 300)).toBeNull();
    expect(defaultInterval(resolved(), 0)).toBeNull();
  });
});

describe("UTC fields", () => {
  it("round-trips the text", () => {
    expect(utcFieldText(base.startMs)).toBe("2026-11-04 08:05");
    expect(parseUtcField("2026-11-04 08:05")).toBe(base.startMs);
    expect(parseUtcField(" 2026-11-04T08:05 ")).toBe(base.startMs);
  });
  it.each(["", "2026-11-04", "04/11/2026 08:05", "2026-11-04 8:05", "2026-13-04 08:05", "2026-02-31 08:05", "2026-11-04 24:00", "2026-11-04 08:60"])(
    "refuses %j",
    (text) => {
      expect(parseUtcField(text)).toBeNull();
    },
  );
});

describe("intervalText", () => {
  it("names the zone and shares the date within a day", () => {
    expect(intervalText("2026-11-04T08:00:00Z", "2026-11-04T10:00:00Z")).toBe("04 Nov 08:00–10:00 UTC");
  });
  it("shows both dates across midnight", () => {
    expect(intervalText("2026-11-04T23:00:00Z", "2026-11-05T01:00:00Z")).toBe(
      "04 Nov 23:00 – 05 Nov 01:00 UTC",
    );
  });
  it("is a dash for an unreadable instant", () => {
    expect(intervalText("x", "2026-11-05T01:00:00Z")).toBe("—");
  });
});

describe("intervalProblem", () => {
  const check = (s: string, e: string) => intervalProblem(s, e, base, 300);
  it("accepts a widened interval", () => {
    expect(check("2026-11-04 08:00", "2026-11-04 10:00")).toBeNull();
    expect(check("2026-11-04 08:05", "2026-11-04 10:00")).toBeNull();
    expect(check("2026-11-04 08:00", "2026-11-04 09:55")).toBeNull();
  });
  it("says the unedited default adds nothing", () => {
    expect(check("2026-11-04 08:05", "2026-11-04 09:55")).toBe("not-widened");
  });
  it("refuses unreadable text, start first", () => {
    expect(check("x", "y")).toBe("start-unreadable");
    expect(check("2026-11-04 08:00", "y")).toBe("end-unreadable");
  });
  it("refuses a bound off the bucket grid", () => {
    expect(check("2026-11-04 08:03", "2026-11-04 10:00")).toBe("unaligned");
    expect(check("2026-11-04 08:00", "2026-11-04 10:02")).toBe("unaligned");
  });
  it("refuses a start that is not before the end", () => {
    expect(check("2026-11-04 10:00", "2026-11-04 10:00")).toBe("start-order");
    expect(check("2026-11-04 11:00", "2026-11-04 10:00")).toBe("start-order");
  });
  it("refuses narrowing on either side", () => {
    expect(check("2026-11-04 08:10", "2026-11-04 10:00")).toBe("start-later");
    expect(check("2026-11-04 08:00", "2026-11-04 09:50")).toBe("end-earlier");
  });
  it("caps the interval at seven days, and allows exactly seven", () => {
    expect(check("2026-10-28 08:05", "2026-11-04 10:00")).toBe("too-long");
    expect(check("2026-10-28 09:55", "2026-11-04 09:55")).toBeNull();
  });
});

describe("permissionOf", () => {
  it("reads the permission from the sentence a 403 carries", () => {
    expect(permissionOf(new ApiError(403, "missing required permission: anomaly:triage"))).toBe(
      "anomaly:triage",
    );
    expect(permissionOf(new ApiError(403, "forbidden"))).toBeNull();
  });
});
