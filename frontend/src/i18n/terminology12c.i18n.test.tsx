import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

import { asTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { estimateText } from "@/components/history/historyFormat";
import { estimateFamily } from "@/components/history/fixtures";

import en from "../../messages/en";
import tr from "../../messages/tr";

// I18N-12c: one Turkish word per concept (B) and the two English text fixes (C).

function flatten(node: unknown, path = ""): Array<[string, string]> {
  if (typeof node === "string") return [[path, node]];
  if (node && typeof node === "object") {
    return Object.entries(node).flatMap(([key, value]) => flatten(value, path ? `${path}.${key}` : key));
  }
  return [];
}

const turkish = new Map(flatten(tr));
const english = new Map(flatten(en));

function say(locale: "en" | "tr"): BehaviorI18n {
  const translate = createTranslator({ locale, messages: (locale === "tr" ? tr : en) as never });
  return { t: asTextTranslator(translate), locale };
}

/** The catalog value of each key the decision names, in the word it settled on. */
const DECIDED: Array<[string, string]> = [
  ["customRules.table.subject", "Konu"],
  ["customRules.picker.kind", "Konu türü"],
  ["customRules.authoring.subjectKinds.LOG_SOURCE", "Log source"],
  ["customRules.authoring.subjectKinds.WAF_ENDPOINT", "WAF endpoint"],
  ["waf.editor.reviewCreate", "Gözden geçir ve oluştur"],
  ["waf.editor.reviewSave", "Gözden geçir ve kaydet"],
  ["ips.editor.reviewCreate", "Gözden geçir ve oluştur"],
  ["ips.editor.reviewSave", "Gözden geçir ve kaydet"],
  ["waf.editor.conflictReload", "Onların sürümünü yeniden yükle"],
  ["ips.editor.reloadTheirs", "Onların sürümünü yeniden yükle"],
  ["pwchart.axis.floor", "Taban {percent}"],
  ["propertyWatch.definition.historyFloor", "Geçmiş tabanı"],
  ["history.table.floor", "taban, UTC"],
  ["enums.ruleHealth.NEVER_OBSERVED", "Hiç gözlenmedi"],
  ["coverage.status.NEVER_OBSERVED", "HİÇ GÖZLENMEDİ"],
  ["waf.endpoints.retired", "Kullanımdan kaldırıldı"],
  ["widgets.options.retired", "kullanımdan kaldırıldı"],
  ["behavior.sourceAnomalies.needsAttention", "Dikkat gerekiyor"],
  ["widgets.range.window", "Pencere"],
  ["widgets.range.windowFor", "{title} için pencere"],
];

describe("one Turkish word per concept", () => {
  it("keeps the word each decided key settled on", () => {
    const wrong = DECIDED.filter(([key, word]) => turkish.get(key) !== word).map(
      ([key, word]) => `${key}: ${String(turkish.get(key))} (want ${word})`,
    );
    expect(wrong).toEqual([]);
  });

  it("never brings the retired variants back", () => {
    const variants: Array<[RegExp, string]> = [
      [/özne/i, "subject is Konu"],
      [/kullanım dışı/i, "retired is Kullanımdan kaldırıldı"],
      [/kullanımdan kalktı/i, "retired is Kullanımdan kaldırıldı"],
      [/gözlemlen/i, "observed is gözlen-"],
      [/nesnesi/i, "a subject kind is the plain term"],
      [/endpoint değeri/i, "a subject kind is the plain term"],
      [/İncele ve (?:oluştur|kaydet)/, "review and create/save is Gözden geçir ve"],
      [/Onların sürümünü yükle/, "reload their version is yeniden yükle"],
      [/Dikkat gerekenler/, "needs attention is Dikkat gerekiyor"],
      [/(?:coverage|geçmiş) alt sınırı/i, "a floor is taban"],
    ];
    const found: string[] = [];
    for (const [key, value] of turkish) {
      for (const [pattern, why] of variants) if (pattern.test(value)) found.push(`${key} (${why})`);
    }
    expect(found).toEqual([]);
  });
});

describe("the two English text fixes", () => {
  it("states the cadence seed truthfully in both languages", () => {
    const empty = english.get("cadence.page.empty") ?? "";
    expect(empty).toContain("All eight families");
    expect(empty).not.toContain("Four families");
    const trEmpty = turkish.get("cadence.page.empty") ?? "";
    expect(trEmpty).toContain("Sekiz ailenin hepsi");
    expect(trEmpty).not.toContain("Dört aile");
  });

  it("pluralises the search count of an estimate", () => {
    const one = estimateText(estimateFamily({ searches: 1, hours: 1 }), say("en"));
    expect(one.figures).toBe("1 search, about 1 hours");
    expect(estimateText(estimateFamily({ searches: 2100, hours: 72 }), say("en")).figures).toBe(
      "2,100 searches, about 72 hours",
    );
    expect(estimateText(estimateFamily({ searches: 0, hours: null }), say("en")).figures).toBe(
      "0 searches, time not estimated",
    );
  });

  it("writes a Turkish search count with the grouping and no plural suffix", () => {
    expect(estimateText(estimateFamily({ searches: 1, hours: null }), say("tr")).figures).toBe(
      "1 arama, süre tahmin edilmedi",
    );
    expect(estimateText(estimateFamily({ searches: 1204, hours: null }), say("tr")).figures).toBe(
      "1.204 arama, süre tahmin edilmedi",
    );
  });
});
