// Canned `GET /geo/countries` answers for the geography tests. Counts are invented and span
// orders of magnitude, like real traffic; no address appears anywhere.

import type { GeoCountries, GeoCountry } from "@/lib/api";

const NOTE =
  "UNRESOLVED counts events whose address is private, internal or unknown to GeoIP. " +
  "The lookup returns the same empty result for all three, so they cannot be told apart.";

export function countries(rows: Array<[string, number]>): GeoCountry[] {
  const total = rows.reduce((sum, [, events]) => sum + events, 0);
  return rows.map(([country_code, events]) => ({ country_code, events, share: events / total }));
}

/** Eighteen countries of mixed size, one of them (SG) without a polygon in the 1:110m outlines. */
export const MIXED: Array<[string, number]> = [
  ["US", 1_240_000],
  ["DE", 402_300],
  ["CN", 310_000],
  ["RU", 188_400],
  ["BR", 96_500],
  ["IN", 71_200],
  ["GB", 55_000],
  ["FR", 41_800],
  ["TR", 23_900],
  ["JP", 12_400],
  ["AU", 8_100],
  ["ZA", 5_200],
  ["MX", 3_300],
  ["NO", 1_900],
  ["CL", 640],
  ["KE", 210],
  ["SG", 95],
  ["IS", 12],
];

export function answer(over: Partial<GeoCountries> = {}): GeoCountries {
  return {
    direction: "SOURCE",
    since: "2026-11-01T10:00:00Z",
    until: "2026-11-02T10:00:00Z",
    countries: countries(MIXED),
    remainders: { UNRESOLVED: 912_400, OVER_CAP: 3_100, UNPARSEABLE: 18 },
    measurement: "EXACT",
    coverage: { measured_buckets: 288, expected_buckets: 288 },
    status: "OK",
    unresolved_note: NOTE,
    country_source: "LOOKUP",
    country_property: null,
    ...over,
  };
}

export const notCollected = (): GeoCountries =>
  answer({
    countries: [],
    remainders: null,
    measurement: null,
    coverage: { measured_buckets: 0, expected_buckets: 288 },
    status: "NOT_COLLECTED",
  });

export const insufficient = (): GeoCountries =>
  answer({
    countries: [],
    remainders: { UNRESOLVED: 40_100, OVER_CAP: 0, UNPARSEABLE: 0 },
    status: "INSUFFICIENT_DATA",
  });

export const partialLowerBound = (): GeoCountries =>
  answer({
    measurement: "LOWER_BOUND",
    coverage: { measured_buckets: 211, expected_buckets: 288 },
    status: "PARTIAL",
    country_source: "PROPERTY",
    country_property: "GeoSourceCountry",
  });
