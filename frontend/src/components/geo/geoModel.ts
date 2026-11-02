// The geography view's pure model: what the map draws and what the table lists, from one
// `GET /geo/countries` answer. No DOM and no ECharts in this file, so every rule is a plain test.
//
// The rules, each with a test:
//   * a country with no row is drawn in the "no data" fill (`--map-0`), never in the scale's
//     lowest stop: a country the collection did not see has no measurement, not a small one;
//   * the scale is a handful of classes with round bounds on a log axis, because country counts
//     span orders of magnitude and a linear ramp would paint everything but the top three the
//     same; the classes are named in the legend with their unit;
//   * a country the 1:110m geometry has no polygon for is listed as not drawn and stays in the
//     table, so the map never silently drops events;
//   * names come from the interface locale (`Intl.DisplayNames`), never from the geometry file.

import type { GeoCountry, GeoCountries, GeoDirection } from "@/lib/api";
import type { FormatLocale } from "@/lib/format";

/** The fills of the ramp, lowest class first: `--map-1` to `--map-4`. */
export const RAMP_TOKENS = ["--map-1", "--map-2", "--map-3", "--map-4"] as const;
/** The fill of a country with no measurement. */
export const NO_DATA_TOKEN = "--map-0";

/** One class of the scale: events from `from` (inclusive) up to `to` (exclusive; null is open). */
export interface ScaleClass {
  /** Position in the ramp, 0 to 3 (`RAMP_TOKENS`). */
  ramp: number;
  from: number;
  to: number | null;
}

/** The largest of 1, 2 and 5 times a power of ten that does not exceed `value` (value >= 1). */
export function roundDown(value: number): number {
  if (!(value >= 1)) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [5, 2, 1]) {
    if (step * power <= value) return step * power;
  }
  return power;
}

/**
 * The classes of the scale for these event counts. Round bounds on a log axis between the
 * smallest and the largest count; a bound that collapses into its neighbour is dropped, so a
 * narrow spread has fewer classes rather than two classes with one bound. No counts, no scale.
 * The classes use the top of the ramp, so a larger class is always the brighter fill.
 */
export function scaleClasses(counts: readonly number[]): ScaleClass[] {
  const positive = counts.filter((n) => Number.isFinite(n) && n > 0);
  if (positive.length === 0) return [];
  const low = Math.min(...positive);
  const high = Math.max(...positive);
  const bounds: number[] = [];
  if (high > low) {
    for (let i = 1; i <= 3; i += 1) {
      const bound = roundDown(low * (high / low) ** (i / 4));
      if (bound > low && bound <= high && !bounds.includes(bound)) bounds.push(bound);
    }
  }
  bounds.sort((a, b) => a - b);
  const starts = [roundDown(low), ...bounds];
  // A single class sits mid-ramp: one class carries no ordering to bright or dim.
  const ramp = (index: number) =>
    starts.length === 1 ? 2 : Math.round((index * (RAMP_TOKENS.length - 1)) / (starts.length - 1));
  return starts.map((from, index) => ({
    ramp: ramp(index),
    from,
    to: index + 1 < starts.length ? starts[index + 1] : null,
  }));
}

/** The class an event count falls in, or null when it is not a positive count. */
export function classOf(events: number, classes: readonly ScaleClass[]): ScaleClass | null {
  if (!(events > 0) || classes.length === 0) return null;
  let found = classes[0];
  for (const candidate of classes) {
    if (events >= candidate.from) found = candidate;
  }
  return found;
}

/** The country's name in the interface locale; the code itself when the runtime cannot name it. */
export function countryName(code: string, locale: FormatLocale): string {
  try {
    const names = new Intl.DisplayNames(locale === "tr" ? "tr-TR" : "en-US", { type: "region" });
    const name = names.of(code);
    return name && name !== code ? name : code;
  } catch {
    return code;
  }
}

export interface GeoRow {
  code: string;
  name: string;
  events: number;
  share: number;
  /** The scale class the map fills it with. */
  ramp: number | null;
}

/** The rows of the table, in the order the API returned them (events, descending). */
export function rowsOf(data: GeoCountries, locale: FormatLocale): GeoRow[] {
  const classes = scaleClasses(data.countries.map((c) => c.events));
  return data.countries.map((c) => ({
    code: c.country_code,
    name: countryName(c.country_code, locale),
    events: c.events,
    share: c.share,
    ramp: classOf(c.events, classes)?.ramp ?? null,
  }));
}

/** The countries of the answer that the geometry holds no polygon for. */
export function undrawn(countries: readonly GeoCountry[], drawable: ReadonlySet<string>): GeoCountry[] {
  return countries.filter((c) => !drawable.has(c.country_code));
}

/** What the map draws for one country: its code, its count and the fill (a ramp position). */
export interface MapDatum {
  /** The ISO alpha-2 code, which names the polygon (`nameProperty: "iso_a2"`). */
  name: string;
  value: number;
  ramp: number;
}

/**
 * The polygons that carry a measurement. A country that is absent here is not given a datum, so
 * the series' own area colour (the no-data fill) is what draws it.
 */
export function mapData(data: GeoCountries, drawable: ReadonlySet<string>): MapDatum[] {
  const classes = scaleClasses(data.countries.map((c) => c.events));
  const out: MapDatum[] = [];
  for (const c of data.countries) {
    if (!drawable.has(c.country_code)) continue;
    const klass = classOf(c.events, classes);
    if (klass) out.push({ name: c.country_code, value: c.events, ramp: klass.ramp });
  }
  return out;
}

export const DIRECTIONS: readonly GeoDirection[] = ["SOURCE", "DESTINATION"];

/** A `direction` query value this version knows, else the default: the source address. */
export function parseDirection(value: string | undefined): GeoDirection {
  return DIRECTIONS.find((d) => d === value) ?? "SOURCE";
}
