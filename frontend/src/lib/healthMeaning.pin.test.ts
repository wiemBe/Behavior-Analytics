// `lib/health.ts` keeps an English table of the verdict meanings (so the pages that import it do
// not carry the catalog); `behavior.health.meaning` holds the same sentences. Neither may drift.
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { healthMeaning } from "./health";

const MEANING = en.behavior.health.meaning as Record<string, string>;

describe("healthMeaning", () => {
  it("has the catalog's English for every verdict, and no verdict the catalog lacks", () => {
    const say = createLocaleTranslator("en");
    for (const [status, sentence] of Object.entries(MEANING)) {
      expect(healthMeaning(status), status).toBe(sentence);
      expect(healthMeaning(status, say), status).toBe(sentence);
    }
    expect(Object.keys(tr.behavior.health.meaning).sort()).toEqual(Object.keys(MEANING).sort());
  });

  it("reads an unknown or inherited verdict as no sentence, never a catalog hit", () => {
    const say = createLocaleTranslator("tr");
    for (const status of ["NEW_VERDICT", "constructor", "toString", "", null, undefined]) {
      expect(healthMeaning(status, say)).toBe("");
      expect(healthMeaning(status)).toBe("");
    }
  });
});
