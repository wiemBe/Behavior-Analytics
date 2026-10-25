// Pure text helpers cannot call React hooks. They accept this small translator shape and default
// to the English catalog so unconverted route callers preserve today's text byte for byte.

import { createTranslator } from "next-intl";

import en from "../../messages/en.json";
import tr from "../../messages/tr.json";
import type { Locale } from "./locale";

export type TextValues = Record<string, string | number | Date>;
export type TextTranslator = (key: string, values?: TextValues) => string;

export function asTextTranslator(translator: unknown): TextTranslator {
  return translator as TextTranslator;
}

export function createLocaleTranslator(locale: Locale): TextTranslator {
  const messages = locale === "tr" ? tr : en;
  const translate = createTranslator({ locale, messages, timeZone: "UTC" });
  return (key, values) => translate(key as never, values as never);
}

export const englishText = createLocaleTranslator("en");
