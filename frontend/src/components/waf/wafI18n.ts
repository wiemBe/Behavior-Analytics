// The words and figures of the WAF area for its pure helpers.
//
// The helpers take a `BehaviorI18n` (a translator over the whole catalog, and the locale) as their
// last, optional argument and default to English, exactly as `lib/behaviorI18n.ts` does for the
// behavior area. Callers outside the area (the anomaly list, the IPS and property-watch pages, the
// Dashboard's option reads) and every existing test therefore keep today's English; route code
// passes its own (`useBehaviorI18n()` in a component, `getBehaviorI18n()` in a server page).
//
// Only the namespaces the WAF helpers read are imported (the area-bundle rule): `waf`, `units`,
// `enums` (the detector-name table) and `behavior` (the evidence words `wafLabels` reuses).

import behavior from "../../../messages/en/behavior.json";
import enums from "../../../messages/en/enums.json";
import units from "../../../messages/en/units.json";
import waf from "../../../messages/en/waf.json";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

/** The English of this area, for the helpers' defaults. */
export const ENGLISH_WAF: BehaviorI18n = {
  t: createTextTranslator("en", { behavior, enums, units, waf }),
  locale: "en",
};

export type { BehaviorI18n };

/** The en dash between the two ends of a range (not a catalog message: it is the same in both). */
export const RANGE_SEP = " – ";

/** The middle dot between two parts of one line (the same in both languages). */
export const MID = " · ";

/** The arrow between the start and the end of an interval. */
export const ARROW_SEP = " → ";
