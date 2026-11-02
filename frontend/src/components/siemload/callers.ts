// The readable name of an Ariel caller code, from the `siemLoad.caller` catalog group (the one
// map). A code the catalog does not know is shown as it was sent, never blank.

import type { TextTranslator } from "@/i18n/textTranslator";

/** `t` is the translator of `useTranslations("siemLoad.caller")`. */
export function callerLabel(t: TextTranslator, code: string): string {
  // `has` follows the prototype chain, and a "." would read as a path into the catalog.
  if (code === "" || code.includes(".") || code in Object.prototype) return code;
  return t.has?.(code) ? t(code) : code;
}
