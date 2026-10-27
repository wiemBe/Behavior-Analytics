// The words of the saved-search pages (`components/searches/*`) in the interface language.
//
// A helper takes a `BehaviorI18n` ({t, locale}) as its LAST optional argument and defaults to this
// English, so a caller that is not converted yet and every existing test keep today's text. The
// components pass their own: `useBehaviorI18n()`; a server page passes `getBehaviorI18n()`.

import cadence from "../../../messages/en/cadence.json";
import enums from "../../../messages/en/enums.json";
import errors from "../../../messages/en/errors.json";
import searches from "../../../messages/en/searches.json";
import units from "../../../messages/en/units.json";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

export const ENGLISH_SEARCHES: BehaviorI18n = {
  t: createTextTranslator("en", { cadence, enums, errors, searches, units }),
  locale: "en",
};

/** A query version as the pages write it: `v3` (a code, the same in both languages). */
export function versionTag(version: number | string): string {
  return `v${version}`;
}
