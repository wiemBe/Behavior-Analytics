// The small translator shape pure text helpers accept, and the one function that builds it from a
// catalog. No catalog is imported here, so importing this file adds no message to any bundle:
// route code converted in I18N-3 and later takes its translator from here (or receives it).

import { createTranslator } from "next-intl";

import type { Locale } from "./locale";

export type TextValues = Record<string, string | number | Date>;

/** `has` is next-intl's own: the translator a component passes keeps it; callers may rely on it. */
export type TextTranslator = ((key: string, values?: TextValues) => string) & {
  has?: (key: string) => boolean;
};

export function asTextTranslator(translator: unknown): TextTranslator {
  return translator as TextTranslator;
}

/** A translator over `messages` (a whole catalog, or the namespaces its caller needs). */
export function createTextTranslator(locale: Locale, messages: Record<string, unknown>): TextTranslator {
  const translate = createTranslator({ locale, messages: messages as never, timeZone: "UTC" });
  const say: TextTranslator = (key, values) => translate(key as never, values as never);
  say.has = (key) => translate.has(key as never);
  return say;
}
