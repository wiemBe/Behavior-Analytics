import { describe, expect, it } from "vitest";

import { createLocaleTranslator } from "@/i18n/testTranslator";

import {
  formatCapacity,
  formatCount,
  formatRate,
  formatSpan,
  kindLabel,
  originFilterLabel,
  typeLabel,
  windowBreakdown,
} from "./ruleStatistics";

const tr = createLocaleTranslator("tr");

describe("rule-statistics text in Turkish", () => {
  it("labels every known type and preserves an unknown value", () => {
    expect(typeLabel("EVENT", tr)).toBe("Olay");
    expect(typeLabel("COMMON", tr)).toBe("Ortak");
    expect(typeLabel("FLOW", tr)).toBe("Akış");
    expect(typeLabel("OFFENSE", tr)).toBe("Offense türü");
    expect(typeLabel("BUILDINGBLOCK", tr)).toBe("Tür bildirilmedi");
    expect(typeLabel("FUTURE_TYPE", tr)).toBe("Future type");
    expect(kindLabel("EVENT", true, tr)).toBe("Olay, yapı taşı");
    expect(originFilterLabel("all", tr)).toBe("Tüm kökenler");
  });

  it("formats table, stat and rate figures with Turkish punctuation", () => {
    expect(formatCount(1234, "tr")).toBe("1.234");
    expect(formatCapacity(1234.5, "tr")).toBe("1.235");
    expect(formatRate(1234.5, "tr")).toBe("1.234,50");
    expect(formatSpan(90061, "tr", tr)).toBe("1\u00a0gün 1\u00a0sa");
  });

  it("translates every PARTIAL and TRUNCATED window contribution", () => {
    expect(windowBreakdown({
      days: 7,
      span_start: "2026-10-15T00:00:00Z",
      span_end: "2026-10-16T00:00:00Z",
      span_seconds: 86400,
      window_seconds: 3600,
      complete: false,
      complete_windows: 1234,
      expected_windows: 1240,
      truncated_windows: 2,
      failed_windows: 3,
      missing_windows: 1,
      measured_seconds: 82800,
      reason: "backend reason",
    }, "tr", tr)).toBe(
      "Eksiksiz pencere sayısı: 1.234\u00a0/\u00a01.240, 2 kırpılmış, 3 başarısız, 1 eksik",
    );
  });
});
