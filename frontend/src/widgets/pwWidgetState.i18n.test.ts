// The pure part of chart.property_watch in Turkish: each early state keeps its kind and its
// `{at}` placeholder, takes the translator and the language as arguments, and falls back to
// English when none is given.

import { describe, expect, it } from "vitest";

import { catalog, watch } from "@/components/propertywatch/fixtures";
import { createLocaleTranslator } from "@/i18n/fallback";

import { qualifiersOf } from "./PropertyWatchWidget";
import { earlyState } from "./pwWidgetState";

const tr = createLocaleTranslator("tr");
const reasonOf = (s: ReturnType<typeof earlyState>) => (s && "reason" in s ? String(s.reason) : "");

describe("earlyState in Turkish", () => {
  it("names the setting when collection is off", () => {
    const s = earlyState(watch({ status: "OK" }), catalog({ collection_enabled: false }), tr, "tr");
    expect(s?.kind).toBe("not_collected");
    expect(reasonOf(s)).toContain("PW_COLLECTION_ENABLED false olduğu için");
    expect(reasonOf(s)).toContain("Geçmişi görmek için izlemeyi açın.");
  });

  it("puts the setting before the API's reason for NOT_ENABLED, and keeps the API's words", () => {
    const s = earlyState(watch({ status: "NOT_ENABLED", status_reason: "switch off" }), null, tr, "tr");
    expect(reasonOf(s)).toBe(
      "PW_COLLECTION_ENABLED false olduğu için hiçbir izleme için arama gönderilmiyor. switch off Geçmişi görmek için izlemeyi açın.",
    );
  });

  it("words the progress of a history read", () => {
    const base = watch();
    const s = earlyState(
      watch({
        status: "READING_HISTORY",
        coverage: null,
        history: { ...base.history, state: "READING", days_requested: 28, days_read: 9 },
      }),
      catalog(),
      tr,
      "tr",
    );
    expect(s?.kind).toBe("empty");
    expect(reasonOf(s)).toBe(
      "Geçmiş QRadar'dan okunuyor: 28 günün 9 günü. Okuma bitene kadar hiçbir şey değerlendirilmez.",
    );
  });

  it("says when the number of days read or requested is not known", () => {
    const base = watch();
    const s = earlyState(
      watch({
        status: "READING_HISTORY",
        coverage: null,
        history: { ...base.history, state: "READING", days_requested: null, days_read: null },
      }),
      catalog(),
      tr,
      "tr",
    );
    expect(reasonOf(s)).toContain("istenen günün bilinmeyen sayıda günü");
  });

  it("keeps the {at} placeholder for the instant, and its time", () => {
    const s = earlyState(watch({ coverage: null, status: null }), catalog(), tr, "tr");
    expect(s?.kind).toBe("empty");
    expect(reasonOf(s)).toContain("Oluşturulma: {at}.");
    expect(reasonOf(s)).toContain("Henüz çalışmadı");
    expect(s && "at" in s && s.at).toBe("2026-10-10T09:00:00Z");
  });

  it("writes the numbers and the coverage of a property that parsed on nothing the Turkish way", () => {
    const s = earlyState(
      watch({
        status: "NOT_APPLICABLE",
        coverage: { bucket_start: "2026-10-18T08:55:00Z", total_events: 1000, parsed_events: 0, ratio: 0.5, measurement: "EXACT" },
      }),
      catalog(),
      tr,
      "tr",
    );
    expect(s?.kind).toBe("no_data");
    expect(reasonOf(s)).toContain("(kapsamdaki 0\u00a0/\u00a01.000 olay)");
    expect(reasonOf(s)).toContain("kapsama oranı %50");
  });

  it("is English when no translator is passed", () => {
    const s = earlyState(watch({ status: "NOT_ENABLED" }), null);
    expect(reasonOf(s)).toContain("PW_COLLECTION_ENABLED is false");
  });

  it("lets a collected, applicable watch through in both languages", () => {
    expect(earlyState(watch(), catalog(), tr, "tr")).toBeNull();
  });
});

describe("qualifiersOf in Turkish", () => {
  const buckets = (n: number, lower = 0) =>
    Array.from({ length: n }, (_, i) => ({
      bucket_start: new Date(Date.parse("2026-10-18T00:00:00Z") + i * 300_000).toISOString(),
      bucket_seconds: 300,
      values_measurement: i < lower ? "LOWER_BOUND" : "EXACT",
    })) as never;

  it("words the gaps and the lower bounds, with their counts", () => {
    const out = qualifiersOf(buckets(284, 3), 24, tr);
    expect(out.map((q) => q.kind)).toEqual(["PARTIAL", "TRUNCATED"]);
    expect(out[0].kind === "PARTIAL" && out[0].text).toBe(
      "284\u00a0/\u00a0288 beş dakikalık aralık toplandı; kalanlar boşluktur, sıfır olay değildir",
    );
    expect(out[1].kind === "TRUNCATED" && out[1].text).toContain("Toplanan zaman dilimlerinden 3\u00a0/\u00a0284 alt sınırdır");
  });

  it("says nothing when the day is complete and exact", () => {
    expect(qualifiersOf(buckets(288), 24, tr)).toEqual([]);
  });
});
