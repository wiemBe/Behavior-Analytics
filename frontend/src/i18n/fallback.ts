// Pure text helpers cannot call React hooks. They accept a small translator (`TextTranslator`) and
// default to English, so a caller that is not converted yet keeps today's English text byte for byte.
//
// RULES (I18N-3):
//   - Route code converted in I18N-3 or a later wave PASSES ITS TRANSLATOR (`useTranslations()`
//     wrapped with `asTextTranslator`) to the pure functions it calls. It never imports
//     `englishText` or anything else from this file. A pure function of a converted route that must
//     still work without a translator (existing callers, tests) defaults to an English translator
//     of its own area, built with `createTextTranslator` from that area's namespaces (see
//     `widgets/englishText.ts`), so the area's English stays in the area's chunk.
//   - This file imports the English of the namespaces its remaining callers use (`auth`: the
//     copy helpers; `shell`, `format`, `common`: ConnectionStatus) and nothing else.
//     `fallback.contract.test.ts` reads the callers and fails when one uses another namespace.
//     ConnectionStatus sits in the shell,
//     on every page: the whole assembly here would put every wave's English into the JavaScript of
//     every page, and the Turkish one would double it. `createLocaleTranslator` therefore holds the
//     whole catalogs in tests only.
//   - The last route wave deletes this file, with the `= englishText` defaults it serves.

import auth from "../../messages/en/auth.json";
import common from "../../messages/en/common.json";
import format from "../../messages/en/format.json";
import shell from "../../messages/en/shell.json";
import type { Locale } from "./locale";
import { createTextTranslator, type TextTranslator } from "./textTranslator";

export { asTextTranslator, type TextTranslator, type TextValues } from "./textTranslator";

/** The English the remaining callers of this file read: the namespaces named in the rules above. */
export const SHARED_ENGLISH = { auth, common, format, shell };

// The catalogs in full are loaded only when Vitest runs (NODE_ENV "test"). The bundler replaces
// NODE_ENV with a constant, drops the branch, and never includes either whole catalog.
const wholeForTests =
  process.env.NODE_ENV === "test"
    ? { en: (await import("../../messages/en")).default, tr: (await import("../../messages/tr")).default }
    : undefined;

export function createLocaleTranslator(locale: Locale): TextTranslator {
  if (locale === "tr") {
    if (!wholeForTests) throw new Error('createLocaleTranslator("tr") is available in tests only');
    return createTextTranslator("tr", wholeForTests.tr);
  }
  return createTextTranslator("en", wholeForTests ? wholeForTests.en : SHARED_ENGLISH);
}

export const englishText = createLocaleTranslator("en");
