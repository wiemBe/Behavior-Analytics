// The words and figures of the behavior area (the overview, the source pages, the anomaly list and
// the anomaly detail) for its pure helpers.
//
// A helper that builds text takes a `BehaviorI18n` as its last argument and defaults to English,
// so every caller outside this area (the WAF, IPS and property-watch pages, the Dashboard) and
// every existing test keeps today's English. Route code passes its own: `useBehaviorI18n()` in a
// component, `getBehaviorI18n()` (./behaviorI18n.server) in a server page. Nothing here translates
// by matching text: a helper returns a code or builds the sentence from a message key.

import behavior from "../../messages/en/behavior.json";
import enums from "../../messages/en/enums.json";
import format from "../../messages/en/format.json";
import units from "../../messages/en/units.json";
import { createTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import type { FormatLocale } from "@/lib/format";
import { isPlainKey } from "@/i18n/ownKey";

export interface BehaviorI18n {
  /** A translator over the whole catalog: full keys, `behavior.state.meaning.OPEN`. */
  t: TextTranslator;
  locale: FormatLocale;
}

/** The English of this area, for the helpers' defaults. */
export const ENGLISH_BEHAVIOR: BehaviorI18n = {
  t: createTextTranslator("en", { behavior, enums, format, units }),
  locale: "en",
};

/** The message at `key`, or the raw `value` when the catalog has none (never an empty string). */
export function wordOrValue(i18n: BehaviorI18n, key: string, value: string): string {
  if (!isPlainKey(value)) return value;
  const full = `${key}.${value}`;
  return i18n.t.has?.(full) ? i18n.t(full) : value;
}

export { fixed, grouped, plainCount } from "@/lib/figureText";

/** An error class as the notices append it: ` (CLASS)`, or nothing. The class is the backend's own name. */
export function errorClassText(errorClass: string | null | undefined): string {
  return errorClass ? ` (${errorClass})` : "";
}

// Table headings made only of technical terms read the same in both languages, so they are not
// catalog messages (the catalog contract allows no identical values beyond a reviewed list).
export const MAD_EPS = "MAD (EPS)";
export const P05_P95_EPS = "p05 – p95 (EPS)";
export const ARROW = "→";

/** An evidence status as a pill shows it: the code in English, the capitals of the word in Turkish. */
export function evidenceCode(i18n: BehaviorI18n, status: string): string {
  return wordOrValue(i18n, "behavior.evidenceStatus", status);
}

/** An evidence status as a word ("Not requested"), as the anomaly list's cells show it. */
export function evidenceWord(i18n: BehaviorI18n, status: string): string {
  return wordOrValue(i18n, "behavior.evidenceWord", status);
}

/** A dimension's availability as a pill shows it. */
export function availabilityCode(i18n: BehaviorI18n, value: string): string {
  return wordOrValue(i18n, "behavior.availability", value);
}

/** A bucket's completeness as a pill shows it. */
export function completenessCode(i18n: BehaviorI18n, value: string): string {
  return wordOrValue(i18n, "behavior.completeness", value);
}

/** A version number as the pages write it: `v3`. */
export function versionLabel(version: number | string): string {
  return `v${version}`;
}

/** A robust-score status as the technical details show it (the code in English). */
export function scoreStatusCode(i18n: BehaviorI18n, status: string): string {
  return wordOrValue(i18n, "behavior.scoreStatus", status);
}

/** The mark on a filter chip that removes it (decorative: the chip's own name says what it does). */
export const CLOSE_MARK = "✕";
