// The words and the number form of the fleet routes' pure helpers (`lib/fleet.ts`).
//
// A helper takes a `FleetText` as its LAST optional argument and defaults to `englishFleetText`, so
// a caller that is not converted yet (the rules manager, the existing tests) keeps today's English.
// Route code passes its own: `useFleetText()` in a component, `fleetTextOf(...)` in a server page.
// `locale` is explicit for the same reason as in `lib/format.ts`: never a runtime default.

import { useLocale, useTranslations } from "next-intl";

import { asTextTranslator, createTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import type { FormatLocale } from "@/lib/format";
import { formatCount } from "@/lib/format";

import catchall from "../../messages/en/catchall.json";
import classification from "../../messages/en/classification.json";
import enums from "../../messages/en/enums.json";
import errors from "../../messages/en/errors.json";
import fleet from "../../messages/en/fleet.json";
import units from "../../messages/en/units.json";

export interface FleetText {
  /** The translator of the whole catalog (full keys: `fleet.page.title`). */
  say: TextTranslator;
  locale: FormatLocale;
}

/** English from the catalog of this area only; the chunk of the fleet routes carries it. */
export const englishFleetText: FleetText = {
  say: createTextTranslator("en", { catchall, classification, enums, errors, fleet, units }),
  locale: "en",
};

export function fleetTextOf(translator: unknown, locale: string): FleetText {
  return { say: asTextTranslator(translator), locale: locale === "tr" ? "tr" : "en" };
}

export function useFleetText(): FleetText {
  return fleetTextOf(useTranslations(), useLocale());
}

/**
 * A count the page printed with no grouping in English (`3 enabled`): English keeps that, Turkish
 * uses its own grouping. Keeps the English byte for byte.
 */
export function plainCount(value: number, locale: FormatLocale): string {
  return locale === "en" ? String(value) : formatCount(value, locale);
}
