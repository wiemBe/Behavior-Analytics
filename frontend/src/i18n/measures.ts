// Figures with their unit, for a given interface language. Every function takes the locale (or a
// translator) explicitly, so a pure function never reads a runtime default (see lib/format.ts).
//
//   - `eps`: events per second to two decimals, grouped and separated by the locale;
//   - `ratio`: a deviation ratio, "1.25x" / "1,25x";
//   - `signedPercent`: a percentage delta already in percent, with a sign and a true minus;
//   - `duration` / `age`: "3 h 20 min" / "12 min ago" with the unit words of the `units` and
//     `format` namespaces.
//
// The missing mark is "—" in both languages and is never a zero.

import { useLocale, useTranslations } from "next-intl";

import type { FormatLocale } from "@/lib/format";
import { formatCount, formatPercent, formatShare } from "@/lib/format";

import type { Locale } from "./locale";

const MISSING = "—";

/**
 * A sentence that opens with an interpolated lowercase noun, started with a capital by the rule of
 * its language: `i` becomes `İ` in Turkish, never `I`. English is left as it is. Messages that can
 * be restructured open with a fixed word instead; this is for the shared `states.*` sentences,
 * whose Turkish values existing tests pin.
 */
export function upperFirst(text: string, locale: FormatLocale): string {
  if (locale !== "tr" || text === "") return text;
  return text.charAt(0).toLocaleUpperCase("tr-TR") + text.slice(1);
}

/**
 * The space between a number and its unit: no-break in Turkish ("968,70 EPS" never splits), the
 * ordinary space in English as before.
 */
export function unitGap(locale: FormatLocale): string {
  return locale === "tr" ? "\u00a0" : " ";
}
const tag = (locale: FormatLocale) => (locale === "tr" ? "tr-TR" : "en-US");

/** EPS to two decimals; a measured 0 reads "0.00", a missing value the em dash. */
export function formatEps(value: number | null | undefined, locale: FormatLocale): string {
  if (value == null || Number.isNaN(value)) return MISSING;
  return value.toLocaleString(tag(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** A ratio with a trailing x ("1.25x", "1,25x"); null is the em dash. */
export function formatRatioLocale(value: number | null | undefined, locale: FormatLocale): string {
  if (value == null || Number.isNaN(value)) return MISSING;
  return `${value.toLocaleString(tag(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false })}x`;
}

/** "+3.2 %" / "−3.9 %" in English, "+%3,2" / "−%3,9" in Turkish; true minus (U+2212). */
export function formatSignedPercent(value: number, locale: FormatLocale): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const magnitude = Math.abs(value).toLocaleString(tag(locale), {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    useGrouping: false,
  });
  return locale === "tr" ? `${sign}%${magnitude}` : `${sign}${magnitude} %`;
}

/** The minimal translator shape: next-intl's `t` for one namespace. */
type Say = (key: string, values?: Record<string, string | number>) => string;

/** "3 h 20 min", "45 s": every non-zero part; null, negative or non-finite is the em dash. */
export function durationText(say: Say, totalSeconds: number | null | undefined): string {
  if (typeof totalSeconds !== "number" || !Number.isFinite(totalSeconds) || totalSeconds < 0) {
    return MISSING;
  }
  const total = Math.round(totalSeconds);
  if (total === 0) return say("second", { count: 0 });
  const parts: string[] = [];
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (days) parts.push(say("day", { count: days }));
  if (hours) parts.push(say("hour", { count: hours }));
  if (minutes) parts.push(say("minute", { count: minutes }));
  if (seconds) parts.push(say("second", { count: seconds }));
  return parts.join(" ");
}

/** "under 1 min ago" .. "2 d ago" from seconds; the em dash for a value that is not an age. */
export function ageText(say: Say, seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return MISSING;
  const whole = Math.floor(seconds);
  if (whole < 60) return say("underMinuteAgo");
  const minutes = Math.floor(whole / 60);
  if (minutes < 60) return say("minutesAgo", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return say("hoursAgo", { count: hours });
  return say("daysAgo", { count: Math.floor(hours / 24) });
}

/** The figures of the interface language, bound to the current locale. */
export function useMeasures() {
  const locale = useLocale() as Locale;
  const units = useTranslations("units") as unknown as Say;
  const format = useTranslations("format") as unknown as Say;
  return {
    locale,
    gap: unitGap(locale),
    count: (value: number | null | undefined) => formatCount(value, locale),
    percent: (value: number | null | undefined, digits = 1) =>
      formatPercent(value, null, digits, locale),
    share: (value: number | null | undefined) => formatShare(value, locale),
    eps: (value: number | null | undefined) => formatEps(value, locale),
    ratio: (value: number | null | undefined) => formatRatioLocale(value, locale),
    signedPercent: (value: number) => formatSignedPercent(value, locale),
    duration: (seconds: number | null | undefined) => durationText(units, seconds),
    age: (seconds: number | null | undefined) => ageText(format, seconds),
  };
}
