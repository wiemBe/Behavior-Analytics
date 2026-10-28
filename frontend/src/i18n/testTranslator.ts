// Tests only: a translator over a whole catalog, so a test can read either language's text without
// a provider. (It replaces the test half of the deleted `fallback.ts`; no source file imports it.)

import en from "../../messages/en";
import tr from "../../messages/tr";
import type { Locale } from "./locale";
import { createTextTranslator, type TextTranslator } from "./textTranslator";

export function createLocaleTranslator(locale: Locale): TextTranslator {
  return createTextTranslator(locale, locale === "tr" ? tr : en);
}
