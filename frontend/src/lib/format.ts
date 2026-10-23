// One count, one share and one percent for every screen.
//
// All are pinned to en-US. `toLocaleString()` with no locale renders the
// server's locale during SSR and the browser's after hydration, so the same
// count can print "1,234" and then "1.234". A missing measurement is an em
// dash, never "0". A share always has one decimal and a space before "%",
// the unit spacing the guidelines require ("12.3 %"); the space is U+00A0, so a
// line never breaks between the number and its sign. A positive share that
// would round to 0.0 % is "<0.1 %", so a real fraction is not drawn as zero.

const COUNT = new Intl.NumberFormat("en-US");

const MISSING = "—";

/** An integer or decimal count. Null, undefined and non-finite values are unmeasured. */
export function formatCount(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  return COUNT.format(value);
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
) {
  if (value == null || Number.isNaN(value)) return MISSING;
  // `measurement === "LOWER_BOUND"` mirrors `isLowerBound` in
  // components/waf/wafLabels: lib/format does not import from components.
  const text =
    digits === 1 ? formatShare(value) : `${(value * 100).toFixed(digits)}\u00A0%`;
  return measurement === "LOWER_BOUND" ? `≥ ${text}` : text;
}

/**
 * A fraction in [0, 1] as a percentage: "12.3 %", "0.0 %", "<0.1 %".
 * Null is unmeasured. A measured zero is "0.0 %", not the missing mark.
 */
export function formatShare(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return MISSING;
  const pct = value * 100;
  if (pct > 0 && pct < 0.1) return "<0.1\u00A0%";
  return `${pct.toFixed(1)}\u00A0%`;
}
