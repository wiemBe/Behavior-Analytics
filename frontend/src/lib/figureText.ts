// Figures as text in the interface language, with no catalog imported (so any chunk may use them).

import type { FormatLocale } from "@/lib/format";

/** A number the way `Number.prototype.toFixed` wrote it in English; Turkish groups and separates by locale. */
export function fixed(value: number, digits: number, locale: FormatLocale): string {
  if (locale === "en") return value.toFixed(digits);
  return value.toLocaleString("tr-TR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
  });
}

/** An integer or decimal with the grouping of the language (`1,234` / `1.234`). */
export function grouped(value: number, locale: FormatLocale): string {
  return value.toLocaleString(locale === "tr" ? "tr-TR" : "en-US");
}

/** A count the way the area printed it before: plain digits in English, grouped in Turkish. */
export function plainCount(value: number, locale: FormatLocale): string {
  return locale === "en" ? String(value) : grouped(value, locale);
}
