// The chart's colours, read from the design tokens.
//
// ECharts draws to a canvas and cannot resolve `var(--x)`, so a colour has to
// reach it as a resolved string. The tokens in `app/tokens.css` stay the single
// source: this reads them once, when a chart is created, rather than mirroring
// them in a second table that could drift. `chartPalette.test.ts` asserts that
// every token named here is declared in `tokens.css`.

/** Palette key -> the custom property that supplies it. */
export const CHART_TOKENS = {
  grid: "--chart-grid",
  axis: "--chart-axis",
  label: "--chart-label",
  observed: "--chart-observed",
  expected: "--chart-expected",
  band: "--chart-band",
  gap: "--chart-gap",
  anomaly: "--chart-anomaly",
  marker: "--chart-marker",
  // Status hues, for marks that ARE those statuses: a failed run, a breached
  // threshold, a query-version change.
  crit: "--crit",
  warn: "--warn",
  tooltipBg: "--chart-tooltip-bg",
  tooltipBorder: "--chart-tooltip-border",
  tooltipText: "--chart-tooltip-text",
  font: "--font",
} as const;

export type ChartPalette = Record<keyof typeof CHART_TOKENS, string>;

/**
 * Resolve every chart token against `root`.
 *
 * Browser-only: it needs a computed style, so call it from an effect, never
 * during render (the component is also rendered on the server). A token that is
 * not defined resolves to an empty string, which ECharts treats as "use the
 * default" rather than throwing.
 */
export function readChartPalette(
  root: Element = document.documentElement,
): ChartPalette {
  const style = getComputedStyle(root);
  const out = {} as ChartPalette;
  for (const key of Object.keys(CHART_TOKENS) as Array<keyof typeof CHART_TOKENS>) {
    out[key] = style.getPropertyValue(CHART_TOKENS[key]).trim();
  }
  return out;
}
