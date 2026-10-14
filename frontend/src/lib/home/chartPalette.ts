// The colours the Home charts add to the shared chart palette (`lib/chartPalette.ts`):
// the categorical series for the class donut, the severity scale for the stacked
// columns, and the surface the donut's slice gap is cut in. Like that module, it
// reads the tokens once, when a chart is created, and `chartPalette.test.ts`'s rule
// applies: every name here must be declared in `tokens.css`.

import { readChartPalette, type ChartPalette } from "@/lib/chartPalette";

export const HOME_TOKENS = {
  cat1: "--chart-cat-1",
  cat2: "--chart-cat-2",
  cat3: "--chart-cat-3",
  cat4: "--chart-cat-4",
  other: "--chart-other",
  surface: "--surface",
  text: "--text",
  critical: "--sev-critical",
  high: "--sev-high",
  medium: "--sev-medium",
  low: "--sev-low",
  info: "--sev-info",
} as const;

export type HomePalette = ChartPalette & Record<keyof typeof HOME_TOKENS, string>;

/** Severity -> palette key. A severity this table does not know is drawn in `info`'s hue. */
export const SEVERITY_TOKEN = {
  CRITICAL: "critical",
  HIGH: "high",
  MEDIUM: "medium",
  LOW: "low",
  INFO: "info",
} as const satisfies Record<string, keyof typeof HOME_TOKENS>;

/** Browser-only, like `readChartPalette`: call it from an effect. */
export function readHomePalette(root: Element = document.documentElement): HomePalette {
  const style = getComputedStyle(root);
  const out = { ...readChartPalette(root) } as HomePalette;
  for (const key of Object.keys(HOME_TOKENS) as Array<keyof typeof HOME_TOKENS>) {
    out[key] = style.getPropertyValue(HOME_TOKENS[key]).trim();
  }
  return out;
}

/** Donut slice colours in order: four categories, then the aggregate. */
export function sliceColors(p: HomePalette): string[] {
  return [p.cat1, p.cat2, p.cat3, p.cat4, p.other];
}

/** `var(--sev-…)` for a severity, for an SVG swatch: CSS never names a severity. */
export function severityVar(severity: string): string {
  const key = SEVERITY_TOKEN[severity as keyof typeof SEVERITY_TOKEN] ?? "info";
  return `var(${HOME_TOKENS[key]})`;
}

/** `var(--chart-…)` for the donut's nth entry, matching `sliceColors`. */
export function sliceVar(index: number): string {
  const keys = ["cat1", "cat2", "cat3", "cat4", "other"] as const;
  return `var(${HOME_TOKENS[keys[Math.min(index, keys.length - 1)]]})`;
}
