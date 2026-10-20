// The windows of a watch page: 1 h, 6 h, 24 h and 7 d. The API accepts 1 to 168 hours; these four
// are the ones offered, in the URL so a view is shareable, and anything else falls back to 24 h.

import type { PwValue } from "@/lib/api";

import { PW_MAX_SERIES } from "./pwSeries";

export const PW_RANGES = [
  { key: "1h", label: "1 hour", hours: 1 },
  { key: "6h", label: "6 hours", hours: 6 },
  { key: "24h", label: "24 hours", hours: 24 },
  { key: "7d", label: "7 days", hours: 168 },
] as const;

/** Values per page of the watch page: a bound on the read, not a loop over values. */
export const VALUES_PAGE_SIZE = 50;

export type PwRangeKey = (typeof PW_RANGES)[number]["key"];

export const DEFAULT_PW_RANGE: PwRangeKey = "24h";

export function parsePwRange(value: string | undefined): (typeof PW_RANGES)[number] {
  return PW_RANGES.find((r) => r.key === value) ?? PW_RANGES[2];
}

const SORTS = ["events", "value", "first_seen"] as const;
export type PwValueSortKey = (typeof SORTS)[number];

export function parsePwSort(value: string | undefined): PwValueSortKey {
  return SORTS.find((s) => s === value) ?? "events";
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Value ids from `?value=`. Ids only (a value's text is never in a URL), at most five. */
export function parsePwSelection(raw: string | string[] | undefined): string[] {
  const list = raw === undefined ? [] : Array.isArray(raw) ? raw : [raw];
  return [...new Set(list.filter((v) => UUID_RE.test(v)))].slice(0, 5);
}

/**
 * The values plotted first: the ones asked for in the URL (by id), else the pinned values, then
 * the values with the most events in the latest bucket. At most five, and a value without a
 * counted reading is never ranked as if it had one.
 */
export function defaultSelection(items: PwValue[], asked: string[]): string[] {
  if (asked.length > 0) return asked.slice(0, PW_MAX_SERIES);
  const pinned = items.filter((v) => v.pinned);
  const counted = items
    .filter((v) => !v.pinned && v.events !== null && v.events > 0)
    .sort((a, b) => (b.events as number) - (a.events as number) || a.id.localeCompare(b.id));
  return [...pinned, ...counted].slice(0, PW_MAX_SERIES).map((v) => v.id);
}

