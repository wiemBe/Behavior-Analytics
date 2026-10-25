// One count, one share and one percent for every screen.
//
// The locale is always an explicit argument with an English default. A runtime
// default locale would differ between SSR and hydration, so it is never
// `undefined`: the same count must not print "1,234" and then "1.234". Existing
// route callers omit it until their route is converted and therefore keep the
// English form. A missing measurement is an em dash, never "0".

export type FormatLocale = "en" | "tr";

const COUNT: Record<FormatLocale, Intl.NumberFormat> = {
  en: new Intl.NumberFormat("en-US"),
  tr: new Intl.NumberFormat("tr-TR"),
};

const MISSING = "—";

/** An integer or decimal count. Null, undefined and non-finite values are unmeasured. */
export function formatCount(
  value: number | null | undefined,
  locale: FormatLocale = "en",
): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  return COUNT[locale].format(value);
}

/**
 * A fraction in [0,1] as a percentage; a lower bound reads "≥ 12.5 %".
 *
 * The one percent formatter for every screen whose figures sit beside sentences
 * the backend wrote with the same spelling (home, catalog, IPS, property watch,
 * WAF). One decimal goes through `formatShare`; another precision keeps the
 * U+00A0 before the sign and does not use the tiny-share mark.
 */
export function formatPercent(
  value: number | null | undefined,
  measurement?: string | null,
  digits = 1,
  locale: FormatLocale = "en",
): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  // `measurement === "LOWER_BOUND"` mirrors `isLowerBound` in
  // components/waf/wafLabels: lib/format does not import from components.
  const text =
    digits === 1
      ? formatShare(value, locale)
      : percentText(value * 100, digits, locale);
  return measurement === "LOWER_BOUND" ? `≥ ${text}` : text;
}

/**
 * A fraction in [0, 1] as a percentage: "12.3 %", "0.0 %", "<0.1 %".
 * Null is unmeasured. A measured zero is "0.0 %", not the missing mark.
 */
export function formatShare(
  value: number | null | undefined,
  locale: FormatLocale = "en",
): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  const pct = value * 100;
  if (pct > 0 && pct < 0.1) return locale === "tr" ? "<%0,1" : "<0.1\u00A0%";
  return percentText(pct, 1, locale);
}

function percentText(value: number, digits: number, locale: FormatLocale): string {
  const number = new Intl.NumberFormat(locale === "tr" ? "tr-TR" : "en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
  }).format(locale === "tr" ? Math.abs(value) : value);
  if (locale === "tr") return `${value < 0 ? "-" : ""}%${number}`;
  return `${number}\u00A0%`;
}
