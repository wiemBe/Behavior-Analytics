// The words of the history backfill (`components/history/*`) in the interface language.
//
// A helper takes a `BehaviorI18n` ({t, locale}) as its LAST optional argument and defaults to this
// English, so a caller that is not converted yet and every existing test keep today's text. The
// components pass their own: `useBehaviorI18n()`.

import enHistory from "../../../messages/en/history.json";
import enUnits from "../../../messages/en/units.json";
import enFormat from "../../../messages/en/format.json";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import type { AgeWords } from "@/lib/time";

export const ENGLISH_HISTORY: BehaviorI18n = {
  t: createTextTranslator("en", { history: enHistory, units: enUnits, format: enFormat }),
  locale: "en",
};

/** The message at `<group>.<value>`, or the raw value when the catalog has none (never empty). */
export function historyWord(i18n: BehaviorI18n, group: string, value: string): string {
  // `has` follows the prototype chain of the catalog objects: "constructor" is never a word.
  if (value === "" || value.includes(".") || value in Object.prototype) return value;
  const key = `history.${group}.${value}`;
  return i18n.t.has?.(key) ? i18n.t(key) : value;
}

/** The `format` namespace's age words, as `relativeAge` takes them. */
export function ageWords(i18n: BehaviorI18n): AgeWords {
  return (key, values) => i18n.t(`format.${key}`, values);
}
