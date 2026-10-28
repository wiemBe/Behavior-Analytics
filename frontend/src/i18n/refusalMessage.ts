// The lean reader for the refusal sentences of MSG-1 (`auth.*`, `dashboard.*`, `propertywatch.*`,
// `customrule.*`).
//
// A refusal can land anywhere: the forced password change sits in the application frame, so its
// reader is part of every page. This module therefore reads only what a refusal parameter can be:
// text, a plain count, or a nested `{code, params}` sentence. It imports no formatter and no
// generated artifact, so it adds the `backend` catalog to the shell and nothing else. A sentence
// whose parameters need more (a percent, a duration, an enum) is not a refusal sentence; it goes
// through `backendMessage.ts`.
//
// The rule is the same as there: English shows the server's text as sent; another locale looks the
// code up in `backend`, and anything absent, malformed, unknown, untranslated or too deep shows the
// server's text. A code is never shown.

import type { RefusalSentence } from "@/lib/refusal";

import type { Locale } from "./locale";
import type { TextTranslator, TextValues } from "./textTranslator";

const CODE_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;
const MAX_DEPTH = 4;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function translate(
  code: string,
  params: Record<string, unknown>,
  say: TextTranslator,
  depth: number,
): string | undefined {
  if (depth > MAX_DEPTH || !CODE_PATTERN.test(code)) return undefined;
  const key = `backend.${code}`;
  if (say.has && !say.has(key)) return undefined;
  const values: TextValues = {};
  for (const [name, value] of Object.entries(params)) {
    if (typeof value === "string") values[name] = value;
    else if (typeof value === "number" && Number.isInteger(value)) values[name] = String(value);
    else if (isRecord(value) && typeof value.code === "string" && isRecord(value.params)) {
      const nested = translate(value.code, value.params, say, depth + 1);
      if (nested === undefined) return undefined;
      values[name] = nested;
    } else return undefined;
  }
  try {
    const words = say(key, values);
    // A translator that cannot resolve the key (an inherited name, a branch of the catalog rather
    // than a sentence) answers with the key itself.
    return words === key ? undefined : words;
  } catch {
    return undefined;
  }
}

/** The sentence in `locale`; the server's own text whenever the code cannot be worded. */
export function renderRefusal(
  sentence: RefusalSentence,
  locale: Locale,
  say: TextTranslator,
): string {
  if (locale === "en" || !sentence.code || !sentence.params) return sentence.text;
  return translate(sentence.code, sentence.params, say, 0) ?? sentence.text;
}
