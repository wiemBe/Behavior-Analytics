// The lean reader for the refusal sentences of MSG-1 (`auth.*`, `dashboard.*`, `propertywatch.*`,
// `customrule.*`).
//
// A refusal can land anywhere: the forced password change sits in the application frame, so its
// reader is part of every page. This module therefore reads only what a refusal parameter can be:
// text, a count, or a nested `{code, params}` sentence. It imports no heavy formatter (only
// `formatCount`, a one-line `Intl.NumberFormat`, so a count is grouped as everywhere else: Turkish
// `1.204`) and no generated artifact, so it adds the `backend` catalog to the shell and nothing
// else. A sentence whose parameters need more (a percent, a duration, an enum) is not a refusal
// sentence; it goes through `backendMessage.ts`.
//
// The first-run dialog of the history backfill (MSG-8a) is mounted in the shell too, and its
// estimate sentences need three more shapes, each named by its parameter: `hours` is a number with
// one decimal (`0,1`, `72,0`), `limits` a list of nested sentences joined with one conjunction, and
// the `noun` enum and any other list are not worded here (the server's text is shown).
//
// The rule is the same as there: English shows the server's text as sent; another locale looks the
// code up in `backend`, and anything absent, malformed, unknown, untranslated or too deep shows the
// server's text. A code is never shown.

import { formatCount } from "@/lib/format";
import type { RefusalSentence } from "@/lib/refusal";

import type { Locale } from "./locale";
import type { TextTranslator, TextValues } from "./textTranslator";

const CODE_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;
const MAX_DEPTH = 4;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Parameters written with one fixed decimal, whatever the value (`72` is `72,0`). */
const ONE_DECIMAL = new Set(["hours"]);
/** An enum parameter is worded from `enums`, which the shell does not carry: the text is shown. */
const UNWORDED = new Set(["noun"]);
/** Parameters that are a tagged list of sentences, joined with one conjunction. */
const CONJUNCTION_LISTS = new Set(["limits"]);

function translate(
  code: string,
  params: Record<string, unknown>,
  locale: Locale,
  say: TextTranslator,
  depth: number,
): string | undefined {
  if (depth > MAX_DEPTH || !CODE_PATTERN.test(code)) return undefined;
  const key = `backend.${code}`;
  if (say.has && !say.has(key)) return undefined;
  const values: TextValues = {};
  for (const [name, value] of Object.entries(params)) {
    if (UNWORDED.has(name)) return undefined;
    if (typeof value === "string") values[name] = value;
    else if (typeof value === "number" && ONE_DECIMAL.has(name) && Number.isFinite(value)) {
      values[name] = value.toLocaleString(locale === "tr" ? "tr-TR" : "en-US", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
    } else if (typeof value === "number" && Number.isInteger(value)) {
      // Every count is grouped in the locale, as `backendMessage.ts` does whatever the style.
      values[name] = formatCount(value, locale);
    } else if (
      CONJUNCTION_LISTS.has(name) &&
      isRecord(value) &&
      value.kind === "list" &&
      Array.isArray(value.items)
    ) {
      const items: string[] = [];
      for (const item of value.items) {
        if (!isRecord(item) || typeof item.code !== "string" || !isRecord(item.params)) return undefined;
        const nested = translate(item.code, item.params, locale, say, depth + 1);
        if (nested === undefined) return undefined;
        items.push(nested);
      }
      values[name] = items.join(locale === "tr" ? " ve " : " and ");
    } else if (isRecord(value) && typeof value.code === "string" && isRecord(value.params)) {
      const nested = translate(value.code, value.params, locale, say, depth + 1);
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
  return translate(sentence.code, sentence.params, locale, say, 0) ?? sentence.text;
}
