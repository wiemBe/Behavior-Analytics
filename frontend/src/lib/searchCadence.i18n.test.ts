import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";

import { ApiError } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { asTextTranslator } from "@/i18n/textTranslator";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { cadenceDuration } from "./cadenceText";
import {
  FAMILY_INFO,
  SCHEDULE_PRESETS,
  cadenceLabel,
  changeFailure,
  everyLabel,
  familyInfo,
  formatBucket,
  outcomeInfo,
  worstCaseDelay,
} from "./searchCadence";

function say(locale: "en" | "tr"): BehaviorI18n {
  const translate = createTranslator({ locale, messages: (locale === "tr" ? tr : en) as never });
  return { t: asTextTranslator(translate), locale };
}

const TR = say("tr");
const NB = " ";

describe("search cadence words in Turkish", () => {
  it("words the seven presets as durations with a no-break unit gap", () => {
    expect(SCHEDULE_PRESETS.map((p) => cadenceLabel(p.seconds, TR))).toEqual([
      `5${NB}dk`,
      `15${NB}dk`,
      `30${NB}dk`,
      `1${NB}sa`,
      `2${NB}sa`,
      `4${NB}sa`,
      `8${NB}sa`,
    ]);
    expect(everyLabel(900, TR)).toBe(`Her 15${NB}dk`);
    expect(everyLabel(900)).toBe("Every 15 min");
  });

  it("keeps a missing duration as the em dash and never as zero", () => {
    expect(cadenceDuration(null, TR)).toBe("—");
    expect(formatBucket(null, TR)).toBe("—");
    expect(formatBucket(300, TR)).toBe(`5${NB}dk`);
  });

  it("words the worst-case delay, with and without buckets", () => {
    expect(worstCaseDelay(300, 300, 120, TR).text).toBe(`en fazla 12${NB}dk`);
    expect(worstCaseDelay(300, null, 120, TR).text).toBe(`kanıt en fazla 7${NB}dk`);
    expect(worstCaseDelay(300, 300, 120).text).toBe("up to 12 min");
    expect(worstCaseDelay(300, null, 120).text).toBe("evidence up to 7 min");
  });

  it("words every family and keeps an unknown family as recorded", () => {
    for (const key of Object.keys(FAMILY_INFO)) {
      const turkish = familyInfo(key, TR);
      expect(turkish.name).not.toBe(FAMILY_INFO[key as keyof typeof FAMILY_INFO].name);
      expect(turkish.scope).toBeTruthy();
      expect(familyInfo(key, say("en"))).toEqual(FAMILY_INFO[key as keyof typeof FAMILY_INFO]);
    }
    expect(familyInfo("PHASE_A_VOLUME", TR).name).toBe("Phase A hacmi");
    expect(familyInfo("NEW_FAMILY", TR)).toEqual({ name: "NEW_FAMILY", scope: null });
    expect(familyInfo("constructor", TR)).toEqual({ name: "constructor", scope: null });
  });

  it("uses the family names of the catalog widget in both languages (one term per concept)", () => {
    const names = (messages: typeof en | typeof tr) =>
      Object.fromEntries(
        Object.entries(messages.cadence.family).map(([key, value]) => [key, value.name]),
      );
    expect(names(en)).toEqual(en.catalog.family);
    expect(names(tr)).toEqual(tr.catalog.family);
  });

  it("words each recorded outcome with its tone, and has no label for an unknown one", () => {
    expect(outcomeInfo("ok", TR)).toEqual({ label: "Tamamlandı", tone: "ok" });
    expect(outcomeInfo("partial", TR)).toEqual({ label: "Kısmi", tone: "warn" });
    expect(outcomeInfo("failed", TR)).toEqual({ label: "Başarısız", tone: "crit" });
    expect(outcomeInfo("no-instance", TR)?.label).toBe("Etkin sistem yok");
    expect(outcomeInfo("no-sources", TR)?.label).toBe("Uygun WAF kaynağı yok");
    expect(outcomeInfo("dispatched", TR)?.label).toBe("Gönderildi");
    expect(outcomeInfo("something-new", TR)).toBeNull();
    expect(outcomeInfo("constructor", TR)).toBeNull();
    expect(outcomeInfo("ok")?.label).toBe("Complete");
  });

  it("says a refusal names the permission and that nothing changed, in Turkish", () => {
    const refused = changeFailure(new ApiError(403, "x"), "Rolünüz bunu yapamıyor (izin: admin:search_cadence).", TR);
    expect(refused).toEqual({
      kind: "forbidden",
      text: "Rolünüz bunu yapamıyor (izin: admin:search_cadence). Hiçbir şey değiştirilmedi.",
    });
    const failed = changeFailure(new ApiError(500, "x"), "unused", TR);
    expect(failed.kind).toBe("error");
    expect(failed.text).toBe(
      "Değiştirilmedi. Sunucu isteği tamamlayamadı. Kısa süre sonra yeniden deneyin.",
    );
    expect(changeFailure(new ApiError(403, "x"), "No.").text).toBe("No. Nothing was changed.");
  });
});
