// Reader for backend-authored sentences with additive message codes.
//
// English always shows the API's text verbatim. Turkish may render a known code
// from the backend namespace, after formatting raw semantic params for the
// locale. Any absent, inherited, malformed, unknown, untranslated, or too-deep
// reference falls back to that same API text; a code is never interface copy.

import { useLocale, useTranslations } from "next-intl";

import { formatPoints } from "@/components/waf/wafFormat";
import { utcTimeText } from "@/components/ui/UtcTime";
import { formatCount, formatPercent, type FormatLocale } from "@/lib/format";

import codesJson from "./backendCodes.generated.json";
import { enumLabel, type EnumTranslator } from "./enumLabels";
import type { Locale } from "./locale";
import { durationText } from "./measures";
import { own } from "./ownKey";
import { asTextTranslator, type TextTranslator } from "./textTranslator";

export interface BackendSentence {
  text: string;
  code?: string | null;
  params?: Record<string, unknown> | null;
}

export interface BackendParamSpec {
  kind:
    | "count"
    | "number"
    | "percent"
    | "points"
    | "duration"
    | "timestamp"
    | "enum"
    | "list"
    | "ref"
    | "text";
  enum_group?: string;
  ref_codes?: string[];
  style?: string;
  precision?: number;
}

export interface BackendCodeSpec {
  template: string;
  params: Record<string, BackendParamSpec>;
  sample: Record<string, unknown>;
  sample_english: string;
  sample_english_params: Record<string, string>;
}

export type BackendCodeArtifact = Record<string, BackendCodeSpec>;

const CODES = codesJson as BackendCodeArtifact;
const MAX_REF_DEPTH = 8;

interface NestedRef {
  code: string;
  params: Record<string, unknown>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

function precision(value: unknown, fallback: number): number {
  return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 6
    ? (value as number)
    : fallback;
}

function numberText(value: number, spec: BackendParamSpec, locale: FormatLocale): string {
  const tag = locale === "tr" ? "tr-TR" : "en-US";
  if (spec.style === "fixed") {
    const precision = spec.precision ?? 1;
    return value.toLocaleString(tag, {
      minimumFractionDigits: precision,
      maximumFractionDigits: precision,
      useGrouping: true,
    });
  }
  // The style shapes the backend's English render only (`general` is Python's `:g`, `grouped`
  // adds commas, `repr` keeps a trailing `.0`). This reader never renders English from params, so
  // every number is grouped in the reader's locale (Turkish `1.204`, `1.260,0`); a declared
  // precision is kept, otherwise six significant digits.
  return value.toLocaleString(tag, {
    ...(spec.precision === undefined
      ? { maximumSignificantDigits: 6 }
      : { maximumFractionDigits: spec.precision }),
    useGrouping: true,
  });
}

function taggedKind(value: unknown, kind: string): value is Record<string, unknown> {
  return isRecord(value) && value.kind === kind;
}

function pointsText(
  value: number,
  digits: number,
  signed: boolean,
  context: RenderContext,
): string {
  if (digits === 1) {
    const formatted = formatPoints(value, { locale: context.locale, t: context.say });
    return signed ? formatted : formatted.replace(/^\+/, "");
  }
  const tag = context.locale === "tr" ? "tr-TR" : "en-US";
  const number = Math.abs(value).toLocaleString(tag, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
  });
  const sign = value < 0 ? "-" : signed && value > 0 ? "+" : "";
  return `${sign}${number}\u00a0${context.say("waf.units.points")}`;
}

function formatUntyped(
  value: unknown,
  context: RenderContext,
  depth: number,
): string | undefined {
  if (typeof value === "string") return value;
  if (finite(value)) {
    return Number.isInteger(value)
      ? formatCount(value, context.locale)
      : numberText(value, { kind: "number" }, context.locale);
  }
  if (typeof value === "boolean") return String(value);
  if (taggedKind(value, "duration") && finite(value.seconds)) {
    return durationText(context.units, value.seconds);
  }
  if (taggedKind(value, "percent") && finite(value.ratio)) {
    return formatPercent(value.ratio, null, precision(value.precision, 0), context.locale);
  }
  if (taggedKind(value, "points") && finite(value.points)) {
    return pointsText(
      value.points,
      precision(value.precision, 1),
      value.signed !== false,
      context,
    );
  }
  if (taggedKind(value, "list") && Array.isArray(value.items)) {
    const items = value.items.map((item) => formatUntyped(item, context, depth));
    if (items.some((item) => item === undefined)) return undefined;
    return new Intl.ListFormat(context.locale === "tr" ? "tr-TR" : "en-US").format(
      items as string[],
    );
  }
  if (isRecord(value) && typeof value.code === "string" && isRecord(value.params)) {
    return translateCode(value as unknown as NestedRef, context, depth + 1);
  }
  return undefined;
}

interface RenderContext {
  locale: Locale;
  say: TextTranslator;
  artifact: BackendCodeArtifact;
  units: (key: string, values?: Record<string, string | number>) => string;
}

/**
 * A list parameter joined as its style says. The style shapes the backend's English; here it
 * keeps the same structure in the reader's language: `and_all` joins with one conjunction between
 * every item (a limit sentence), and `semicolon` joins the clauses of a history reason with
 * "; " as the English does. The `semicolon` lists of the earlier families (rule health) take the
 * locale's own list form in a non-English reader, as their catalogs were written for.
 */
function joinedList(
  items: unknown[],
  spec: BackendParamSpec,
  context: RenderContext,
  depth: number,
  code: string,
): string | undefined {
  const parts = items.map((item) => formatUntyped(item, context, depth));
  if (parts.some((part) => part === undefined)) return undefined;
  const texts = parts as string[];
  if (spec.style === "semicolon" && code.startsWith("history.")) return texts.join("; ");
  if (spec.style === "and_all") return texts.join(context.locale === "tr" ? " ve " : " and ");
  return new Intl.ListFormat(context.locale === "tr" ? "tr-TR" : "en-US").format(texts);
}

function formatParam(
  value: unknown,
  spec: BackendParamSpec,
  context: RenderContext,
  depth: number,
  code: string,
): string | undefined {
  switch (spec.kind) {
    case "count":
      if (!Number.isInteger(value)) return undefined;
      // Every count is grouped in the locale, whatever its style: the `plain` style only keeps
      // the backend's English render byte for byte, and this reader never renders English from
      // params (English shows the API text verbatim).
      return formatCount(value as number, context.locale);
    case "number":
      return finite(value) ? numberText(value, spec, context.locale) : undefined;
    case "percent":
      return taggedKind(value, "percent") && finite(value.ratio)
        ? formatPercent(
            value.ratio,
            null,
            precision(value.precision, spec.precision ?? 0),
            context.locale,
          )
        : undefined;
    case "points":
      return taggedKind(value, "points") && finite(value.points)
        ? pointsText(
            value.points,
            precision(value.precision, spec.precision ?? 1),
            value.signed !== false,
            context,
          )
        : undefined;
    case "duration":
      return taggedKind(value, "duration") && finite(value.seconds)
        ? durationText(context.units, value.seconds)
        : undefined;
    case "timestamp": {
      if (typeof value !== "string") return undefined;
      const full = utcTimeText(value);
      if (full === "—") return undefined;
      // The `minute` style is date, hour:minute and UTC; the default keeps its seconds.
      return spec.style === "minute" ? full.replace(/(\d\d:\d\d):\d\d( UTC)$/, "$1$2") : full;
    }
    case "enum": {
      if (typeof value !== "string" || !spec.enum_group) return undefined;
      const label = enumLabel(context.say as EnumTranslator, spec.enum_group as never, value);
      // `lower` is the style of a sentence that always spelled the value in lowercase: the
      // group's labels are lowercase already, and a value the catalog lacks is lowercased
      // here (in the reader's locale) so it does not stand out of the sentence in capitals.
      return spec.style === "lower" && label === value
        ? label.toLocaleLowerCase(context.locale === "tr" ? "tr-TR" : "en-US")
        : label;
    }
    case "list":
      return taggedKind(value, "list") && Array.isArray(value.items)
        ? joinedList(value.items, spec, context, depth, code)
        : undefined;
    case "ref":
      if (typeof value === "string") return value;
      if (!isRecord(value) || typeof value.code !== "string" || !isRecord(value.params)) {
        return undefined;
      }
      if (!spec.ref_codes?.includes(value.code)) return undefined;
      return translateCode(value as unknown as NestedRef, context, depth + 1);
    case "text":
      return typeof value === "string" ? value : undefined;
  }
}

function translateCode(
  ref: NestedRef,
  context: RenderContext,
  depth: number,
): string | undefined {
  if (depth > MAX_REF_DEPTH) return undefined;
  const spec = own(context.artifact, ref.code);
  if (!spec) return undefined;
  const key = `backend.${ref.code}`;
  if (!context.say.has?.(key)) return undefined;
  const expected = Object.keys(spec.params);
  if (
    Object.keys(ref.params).length !== expected.length ||
    expected.some((name) => !Object.hasOwn(ref.params, name))
  ) {
    return undefined;
  }
  const formatted: Record<string, string> = {};
  for (const name of expected) {
    const value = formatParam(ref.params[name], spec.params[name], context, depth, ref.code);
    if (value === undefined) return undefined;
    formatted[name] = value;
  }
  try {
    const translated = context.say(key, formatted);
    return translated === key ? undefined : translated;
  } catch {
    return undefined;
  }
}

export function renderBackendMessage(
  sentence: BackendSentence,
  locale: Locale,
  say: TextTranslator,
  artifact: BackendCodeArtifact = CODES,
): string {
  if (locale === "en" || !sentence.code || !sentence.params) return sentence.text;
  return renderBackendCode(
    { code: sentence.code, params: sentence.params },
    locale,
    say,
    artifact,
  ) ?? sentence.text;
}

/** Render a known code directly; exported so the artifact contract can pin both locales. */
export function renderBackendCode(
  ref: NestedRef,
  locale: Locale,
  say: TextTranslator,
  artifact: BackendCodeArtifact = CODES,
): string | undefined {
  const units = (key: string, values?: Record<string, string | number>) => say(`units.${key}`, values);
  return translateCode(ref, { locale, say, artifact, units }, 0);
}

export function useBackendMessage() {
  const locale = useLocale() as Locale;
  const say = asTextTranslator(useTranslations());
  return (sentence: BackendSentence) => renderBackendMessage(sentence, locale, say);
}
