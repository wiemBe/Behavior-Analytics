// The words of the search cadence and the schedule labels (`lib/searchCadence.ts`) and the
// executions' units, in the interface language.
//
// A helper takes a `BehaviorI18n` ({t, locale}) as its LAST optional argument and defaults to this
// English, so every caller that is not converted yet (the property-watch pages, the catalog
// widgets, the existing tests) keeps today's text. Route code passes its own: `useBehaviorI18n()`
// in a component, `getBehaviorI18n()` in a server page.

import cadence from "../../messages/en/cadence.json";
import errors from "../../messages/en/errors.json";
import units from "../../messages/en/units.json";
import { durationText } from "@/i18n/measures";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

export const ENGLISH_CADENCE: BehaviorI18n = {
  t: createTextTranslator("en", { cadence, errors, units }),
  locale: "en",
};

/** `5 min`, `2 h 5 min`, `5 dk`; the em dash for a value that is not a duration. */
export function cadenceDuration(
  seconds: number | null | undefined,
  i18n: BehaviorI18n = ENGLISH_CADENCE,
): string {
  return durationText((key, values) => i18n.t(`units.${key}`, values), seconds);
}
