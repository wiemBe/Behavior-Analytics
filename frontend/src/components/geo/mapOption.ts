// The ECharts option of the choropleth, built from the model. Pure: the palette and the words
// arrive as arguments, so every rule is a plain test and nothing here touches the DOM.
//
//   * the series' own area colour is the "no data" fill (`--map-0`): a polygon with no datum is
//     drawn by it, so a country the answer does not list can never take a scale colour;
//   * a country with a row is given its class's fill, one colour per datum, with no visual-map
//     component (the legend is HTML beside the map, which a screen reader can read);
//   * the hover state keeps the country's own fill and only draws a rim: ECharts' default hover
//     fill is a yellow that is not in the palette;
//   * the tooltip names the country in the interface locale, never from the geometry file.

import type { EChartsOption } from "echarts";

import type { GeoCountries } from "@/lib/api";

import { mapData, NO_DATA_TOKEN, RAMP_TOKENS } from "./geoModel";

/** The name the geometry is registered under. */
export const MAP_NAME = "geo-world";

/** The tokens the map reads, resolved once when the chart is created. */
export const MAP_TOKENS = {
  noData: NO_DATA_TOKEN,
  ramp0: RAMP_TOKENS[0],
  ramp1: RAMP_TOKENS[1],
  ramp2: RAMP_TOKENS[2],
  ramp3: RAMP_TOKENS[3],
  border: "--surface",
  rim: "--text",
  tooltipBg: "--chart-tooltip-bg",
  tooltipBorder: "--chart-tooltip-border",
  tooltipText: "--chart-tooltip-text",
  font: "--font",
} as const;

export type MapPalette = Record<keyof typeof MAP_TOKENS, string>;

/** Browser-only, like `readChartPalette`: call it from an effect. */
export function readMapPalette(root: Element = document.documentElement): MapPalette {
  const style = getComputedStyle(root);
  const out = {} as MapPalette;
  for (const key of Object.keys(MAP_TOKENS) as Array<keyof typeof MAP_TOKENS>) {
    out[key] = style.getPropertyValue(MAP_TOKENS[key]).trim();
  }
  return out;
}

/** The fills of the ramp, lowest class first. */
export function rampColors(p: MapPalette): string[] {
  return [p.ramp0, p.ramp1, p.ramp2, p.ramp3];
}

/** The words a tooltip uses, already in the interface language. */
export interface MapText {
  noData: string;
  /** "1,240 events", or "At least 1,240 events" when the counts are lower bounds. */
  events: (count: number, atLeast: boolean) => string;
  /** "12.3 % of the events with a country". */
  share: (share: number) => string;
  /** The country's name in the interface locale. */
  name: (code: string) => string;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export interface MapOptionInput {
  data: GeoCountries;
  /** The ISO codes the registered geometry holds a polygon for. */
  drawable: ReadonlySet<string>;
  palette: MapPalette;
  text: MapText;
  reduceMotion: boolean;
}

export function buildMapOption({
  data,
  drawable,
  palette,
  text,
  reduceMotion,
}: MapOptionInput): EChartsOption {
  const ramp = rampColors(palette);
  const byCode = new Map(data.countries.map((c) => [c.country_code, c]));
  const atLeast = data.measurement === "LOWER_BOUND";
  const noData = palette.noData;
  const rim = { borderColor: palette.rim, borderWidth: 1 };
  return {
    animation: !reduceMotion,
    textStyle: { fontFamily: palette.font },
    tooltip: {
      trigger: "item",
      confine: true,
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontFamily: palette.font },
      extraCssText: "box-shadow: none;",
      formatter: (params: unknown) => {
        const code = String((params as { name?: unknown }).name ?? "");
        if (!code) return "";
        const row = byCode.get(code);
        const lines = [`<strong>${escapeHtml(text.name(code))}</strong>`];
        if (!row) lines.push(escapeHtml(text.noData));
        else {
          lines.push(escapeHtml(text.events(row.events, atLeast)));
          lines.push(escapeHtml(text.share(row.share)));
        }
        return lines.join("<br/>");
      },
    },
    series: [
      {
        type: "map",
        map: MAP_NAME,
        nameProperty: "iso_a2",
        roam: false,
        // The map series squeezes longitude by 0.75 by default; 0.85 keeps shapes recognisable
        // without the stretch of a plate carrée, and fits the 2.15:1 plot.
        aspectScale: 0.85,
        selectedMode: false,
        // The world between 85 N and 58 S: Antarctica would take a fifth of the plot for no data.
        boundingCoords: [
          [-180, 85],
          [180, -58],
        ],
        itemStyle: { areaColor: noData, borderColor: palette.border, borderWidth: 0.5 },
        emphasis: {
          label: { show: false },
          itemStyle: { areaColor: noData, ...rim },
        },
        label: { show: false },
        data: mapData(data, drawable).map((d) => ({
          name: d.name,
          value: d.value,
          itemStyle: { areaColor: ramp[d.ramp] },
          emphasis: { itemStyle: { areaColor: ramp[d.ramp], ...rim } },
        })),
      },
    ],
  };
}
