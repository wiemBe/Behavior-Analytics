import { describe, expect, it } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import { createLocaleTranslator } from "@/i18n/fallback";
import { ApiError, type FleetClassSummary } from "@/lib/api";
import {
  ALL_CLASSES,
  BASIS_OPTIONS,
  CLASS_LABELS,
  FLEET_WINDOWS,
  activeFilterLabels,
  basisLabel,
  classLabel,
  completenessInfo,
  conflictText,
  expectedInfo,
  formatEpsValue,
  formatEventCount,
  openIncidentList,
  originLabel,
  reviewLabel,
  sourceFailure,
  windowName,
} from "@/lib/fleet";
import { englishFleetText, fleetTextOf, plainCount } from "@/lib/fleetText";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { createTextTranslator } from "@/i18n/textTranslator";

void setTestLocale;

const turkish = fleetTextOf(createTextTranslator("tr", tr), "tr");

function cls(over: Partial<FleetClassSummary> = {}): FleetClassSummary {
  return {
    source_class: "WAF",
    sources: { enabled: 2, disabled: 0 },
    buckets: { expected: 288, complete: 270, complete_from_history: 0, partial: 10, missing: 8 },
    events: 1_204_000,
    mean_eps: 1234.5,
    expected_eps: 1500,
    baseline_sources: 2,
    baseline_share: 0.5,
    monitored_sources: 4,
    open_incidents: {},
    top_sources: [],
    ...over,
  };
}

describe("class labels", () => {
  it("has an English and a Turkish word for every class, and the English is today's table", () => {
    for (const c of ALL_CLASSES) {
      expect(classLabel(c)).toBe(CLASS_LABELS[c]);
      expect(classLabel(c, turkish), c).not.toBe("");
    }
    expect(classLabel("FIREWALL", turkish)).toBe("Güvenlik duvarı");
    expect(classLabel("QRADAR_INTERNAL", turkish)).toBe("QRadar dahili");
    expect(classLabel("UNCLASSIFIED", turkish)).toBe("Sınıflanmamış");
  });

  it("shows a class this build does not know as sent, and keeps the missing mark", () => {
    expect(classLabel("QUANTUM", turkish)).toBe("QUANTUM");
    expect(classLabel("a.b", turkish)).toBe("a.b");
    expect(classLabel(null, turkish)).toBe("—");
  });
});

describe("windows", () => {
  it("names each window in both languages", () => {
    expect(FLEET_WINDOWS.map((w) => windowName(w.value))).toEqual(["1 hour", "24 hours", "7 days"]);
    expect(FLEET_WINDOWS.map((w) => windowName(w.value, turkish))).toEqual([
      "1 saat",
      "24 saat",
      "7 gün",
    ]);
  });
});

describe("figures", () => {
  it("writes EPS and counts in the Turkish number form, English unchanged", () => {
    expect(formatEpsValue(1234.5)).toBe("1,234.50");
    expect(formatEpsValue(1234.5, "tr")).toBe("1.234,50");
    expect(formatEpsValue(0, "tr")).toBe("0,00");
    expect(formatEpsValue(null, "tr")).toBeNull();
    expect(formatEventCount(1_204_000)).toBe("1,204,000");
    expect(formatEventCount(1_204_000, "tr")).toBe("1.204.000");
  });

  it("words the completeness of the window, partial and missing counts included", () => {
    expect(completenessInfo(cls().buckets)).toEqual({
      state: "incomplete",
      text: "270 of 288 buckets complete",
      detail: "10 partial, 8 missing",
    });
    expect(completenessInfo(cls().buckets, turkish)).toEqual({
      state: "incomplete",
      text: "270 / 288 zaman dilimi tamamlandı",
      detail: "10 kısmi, 8 eksik",
    });
    expect(completenessInfo({ ...cls().buckets, expected: 0 }, turkish)).toMatchObject({
      state: "none",
      text: "İzlenen kaynak yok",
    });
    const whole = completenessInfo({ expected: 1_000, complete: 1_000, complete_from_history: 0, partial: 0, missing: 0 }, turkish);
    expect(whole).toMatchObject({ state: "complete", text: "1.000 / 1.000 zaman dilimi tamamlandı" });
  });

  it("words the expectation with its basis, and says when there is none", () => {
    expect(expectedInfo(cls()).note).toBe("From 2 of 4 sources with a baseline.");
    expect(expectedInfo(cls(), turkish).note).toBe("Baseline'ı olan kaynaklar: 2 / 4.");
    expect(expectedInfo(cls({ baseline_share: null }), turkish).note).toBe("Baseline'ı olan 2 kaynaktan.");
    const none = expectedInfo(cls({ expected_eps: null }), turkish);
    expect(none.value).toBeNull();
    expect(none.note).toBe("Henüz hiçbir kaynağın baseline'ı yok.");
  });

  it("labels the open incidents by the anomaly-type table, and keeps an unknown type as sent", () => {
    const c = cls({ open_incidents: { NO_EVENTS: 2, VOLUME_SPIKE: 1, SOMETHING_NEW: 3 } });
    expect(openIncidentList(c)).toEqual([
      ["SOMETHING_NEW", 3],
      ["No events", 2],
      ["Volume spike", 1],
    ]);
    expect(openIncidentList(c, turkish).map(([label]) => label)).toEqual([
      "SOMETHING_NEW",
      "Olay yok",
      "Hacim artışı",
    ]);
  });
});

describe("classification in words", () => {
  it("has a word for every basis in both languages; the English is today's wording", () => {
    for (const b of BASIS_OPTIONS) {
      expect(basisLabel(b)).not.toBe(b);
      expect(basisLabel(b, turkish)).not.toBe(basisLabel(b));
    }
    expect(basisLabel("OVERRIDE")).toBe("Operator override");
    expect(basisLabel("TYPE_NAME_REGEX")).toBe("Type-name rule");
    expect(basisLabel("OVERRIDE", turkish)).toBe("Operatör geçersiz kılması");
    expect(basisLabel("FUTURE_BASIS", turkish)).toBe("FUTURE_BASIS");
  });

  it("reads a conflict as 'subject says class', in both languages", () => {
    const source = {
      basis: "TYPE" as const,
      source_class: "IPS" as const,
      conflicts: [{ basis: "NAME_REGEX" as const, rule_id: "r2", class: "LINUX" as const }],
    };
    expect(conflictText(source)).toBe("type says IPS, name says Linux");
    expect(conflictText(source, turkish)).toBe("tür: IPS, ad: Linux");
    expect(conflictText({ ...source, conflicts: [] }, turkish)).toBe("");
  });

  it("words the review status and the origin, including not recorded", () => {
    expect([reviewLabel("NEW"), reviewLabel("REVIEWED"), reviewLabel(null)]).toEqual([
      "New",
      "Reviewed",
      "Not recorded",
    ]);
    expect([reviewLabel("NEW", turkish), reviewLabel("REVIEWED", turkish), reviewLabel(null, turkish)]).toEqual([
      "Yeni",
      "İncelendi",
      "Kaydedilmedi",
    ]);
    expect([originLabel(null), originLabel(true), originLabel(false)]).toEqual([
      "Origin not recorded",
      "Auto-discovered",
      "Configured",
    ]);
    expect([originLabel(null, turkish), originLabel(true, turkish), originLabel(false, turkish)]).toEqual([
      "Köken kaydedilmedi",
      "Otomatik keşfedildi",
      "Yapılandırıldı",
    ]);
  });

  it("words the active filters as chips", () => {
    const q = { class: "LINUX", review_status: "NEW", has_conflict: "true", basis: "GROUP", offset: 0 } as const;
    expect(activeFilterLabels(q)).toEqual([
      "Class: Linux",
      "Review: New",
      "Conflicting only",
      "Basis: Group rule",
    ]);
    expect(activeFilterLabels(q, turkish)).toEqual([
      "Sınıf: Linux",
      "İnceleme: Yeni",
      "Yalnızca çakışanlar",
      "Dayanak: Grup kuralı",
    ]);
  });
});

describe("failures", () => {
  it("names the permission on 403 and says nothing changed, in both languages", () => {
    expect(sourceFailure(new ApiError(403, "no")).text).toContain("permission log_source:write");
    expect(sourceFailure(new ApiError(403, "no"), turkish)).toEqual({
      kind: "forbidden",
      text: "Rolünüz bir kaynağın sınıflandırmasını değiştiremiyor (izin: log_source:write). Hiçbir şey değiştirilmedi.",
    });
  });

  it("starts any other failure with 'not changed' and the shared reason; a 422 detail stays as the server sent it", () => {
    expect(sourceFailure(new ApiError(500, "x"), turkish).text).toBe(
      "Değiştirilmedi. Sunucu isteği tamamlayamadı. Kısa süre sonra yeniden deneyin.",
    );
    expect(sourceFailure(new ApiError(422, "Class is not assignable."), turkish).text).toBe(
      "Değiştirilmedi. Class is not assignable.",
    );
    expect(sourceFailure(new Error("x")).text).toBe(
      "Not changed. The request could not be sent. Check your connection and try again.",
    );
  });
});

describe("plain counts", () => {
  it("keeps the English count ungrouped as the pages always printed it, and groups it in Turkish", () => {
    expect(plainCount(1204, "en")).toBe("1204");
    expect(plainCount(1204, "tr")).toBe("1.204");
    expect(plainCount(0, "tr")).toBe("0");
  });
});

describe("English default", () => {
  it("is the English of the catalog, from the area's own translator", () => {
    expect(englishFleetText.say("fleet.page.title")).toBe(en.fleet.page.title);
    expect(createLocaleTranslator("en")("fleet.page.title")).toBe(en.fleet.page.title);
  });
});
