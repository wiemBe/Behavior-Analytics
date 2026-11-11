import { describe, expect, it } from "vitest";

import {
  alertsDrillHref,
  anomaliesDrillHref,
  calendarStart,
  endsAtNow,
  isCurrentPeriod,
  parseReportQuery,
  periodExportParams,
  reportHref,
  stepStart,
  subjectKindOf,
} from "./reports";

// Wednesday 2026-11-11 09:00 UTC.
const NOW = Date.parse("2026-11-11T09:00:00Z");

describe("calendarStart", () => {
  it("is Monday 00:00 UTC for a week, whatever day inside it", () => {
    expect(calendarStart("calendar_week", NOW)).toBe("2026-11-09T00:00:00.000Z");
    expect(calendarStart("calendar_week", Date.parse("2026-11-15T23:59:00Z"))).toBe("2026-11-09T00:00:00.000Z");
    expect(calendarStart("calendar_week", Date.parse("2026-11-09T00:00:00Z"))).toBe("2026-11-09T00:00:00.000Z");
  });
  it("is the 1st 00:00 UTC for a month", () => {
    expect(calendarStart("calendar_month", NOW)).toBe("2026-11-01T00:00:00.000Z");
  });
});

describe("stepStart", () => {
  it("steps a week by seven days and a month by calendar month, across the year", () => {
    expect(stepStart("calendar_week", "2026-11-09T00:00:00.000Z", -1)).toBe("2026-11-02T00:00:00.000Z");
    expect(stepStart("calendar_month", "2026-01-01T00:00:00.000Z", -1)).toBe("2025-12-01T00:00:00.000Z");
    expect(stepStart("calendar_month", "2025-12-01T00:00:00.000Z", 1)).toBe("2026-01-01T00:00:00.000Z");
  });
});

describe("parseReportQuery", () => {
  it("defaults to the last 7 days", () => {
    expect(parseReportQuery({}, NOW)).toEqual({ period: "last_7d", start: null, issues: [] });
  });
  it("drops an unknown period and says so", () => {
    expect(parseReportQuery({ period: "yearly" }, NOW)).toEqual({
      period: "last_7d",
      start: null,
      issues: ["unknownPeriod"],
    });
  });
  it("ignores a start on a sliding period", () => {
    expect(parseReportQuery({ period: "last_30d", start: "2026-11-02T00:00:00Z" }, NOW).start).toBeNull();
  });
  it("starts a calendar period at the current one", () => {
    expect(parseReportQuery({ period: "calendar_week" }, NOW).start).toBe("2026-11-09T00:00:00.000Z");
    expect(parseReportQuery({ period: "calendar_month" }, NOW).start).toBe("2026-11-01T00:00:00.000Z");
  });
  it("keeps an earlier boundary", () => {
    const q = parseReportQuery({ period: "calendar_week", start: "2026-11-02T00:00:00Z" }, NOW);
    expect(q.start).toBe("2026-11-02T00:00:00.000Z");
    expect(q.issues).toEqual([]);
  });
  it("replaces a start that is not a boundary", () => {
    const q = parseReportQuery({ period: "calendar_week", start: "2026-11-03T00:00:00Z" }, NOW);
    expect(q.start).toBe("2026-11-09T00:00:00.000Z");
    expect(q.issues).toEqual(["badStart"]);
    expect(parseReportQuery({ period: "calendar_month", start: "nonsense" }, NOW).issues).toEqual(["badStart"]);
  });
  it("never goes past the current period", () => {
    const week = parseReportQuery({ period: "calendar_week", start: "2026-11-16T00:00:00Z" }, NOW);
    expect(week.start).toBe("2026-11-09T00:00:00.000Z");
    expect(week.issues).toEqual(["futureStart"]);
    expect(parseReportQuery({ period: "calendar_month", start: "2026-12-01T00:00:00Z" }, NOW).issues).toEqual([
      "futureStart",
    ]);
  });
});

describe("reportHref", () => {
  it("leaves the default period and the current calendar period bare", () => {
    expect(reportHref("last_7d", null, NOW)).toBe("/reports");
    expect(reportHref("last_30d", null, NOW)).toBe("/reports?period=last_30d");
    expect(reportHref("calendar_week", calendarStart("calendar_week", NOW), NOW)).toBe("/reports?period=calendar_week");
  });
  it("carries the start of an earlier calendar period", () => {
    expect(reportHref("calendar_month", "2026-10-01T00:00:00.000Z", NOW)).toBe(
      "/reports?period=calendar_month&start=2026-10-01T00%3A00%3A00Z",
    );
  });
});

describe("isCurrentPeriod", () => {
  it("is true only for the period that holds now", () => {
    expect(isCurrentPeriod("calendar_week", "2026-11-09T00:00:00.000Z", NOW)).toBe(true);
    expect(isCurrentPeriod("calendar_week", "2026-11-02T00:00:00.000Z", NOW)).toBe(false);
  });
});

describe("drill links", () => {
  const range = { start: "2026-11-04T09:00:00.000Z", end: "2026-11-11T09:00:00.000Z" };
  it("sends the list's own parameter names for alerts", () => {
    const url = new URL(alertsDrillHref({ family: "log_source", severity: "HIGH" }, range), "http://x");
    expect(url.pathname).toBe("/alerts");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      family: "log_source",
      severity: "HIGH",
      since: "2026-11-04T09:00:00Z",
      until: "2026-11-11T09:00:00Z",
    });
  });
  it("filters anomalies by subject kind, and IPS by its URL-only value", () => {
    const url = new URL(anomaliesDrillHref({ family: "waf", severity: "MEDIUM" }, range), "http://x");
    expect(url.pathname).toBe("/anomalies");
    expect(url.searchParams.get("subject_kind")).toBe("WAF_ENDPOINT");
    expect(url.searchParams.get("severity")).toBe("MEDIUM");
    expect(subjectKindOf("ips")).toBe("IPS");
    expect(subjectKindOf("property_watch")).toBe("PW_VALUE");
    expect(subjectKindOf("rule")).toBe("RULE");
    expect(subjectKindOf("log_source")).toBe("LOG_SOURCE");
  });
  it("exports the period as the list's since and until", () => {
    expect(periodExportParams(range)).toEqual({ since: "2026-11-04T09:00:00Z", until: "2026-11-11T09:00:00Z" });
  });
});

describe("endsAtNow", () => {
  it("is true when the range was clamped to the read time", () => {
    expect(endsAtNow({ end: "2026-11-11T09:00:00Z", generated_at: "2026-11-11T09:00:00Z" })).toBe(true);
    expect(endsAtNow({ end: "2026-11-09T00:00:00Z", generated_at: "2026-11-11T09:00:00Z" })).toBe(false);
  });
});
