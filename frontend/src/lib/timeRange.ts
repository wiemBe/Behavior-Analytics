// The time range of a list view (SOC-7b): relative chips and a Custom window, kept in the URL.
//
// A chip is relative and stays relative: `range=24h` means "the last 24 h when the link is
// opened", so a shared link never freezes the sender's clock. Custom writes absolute UTC
// `since` / `until`. Nothing here trusts the URL: an unknown chip, an unreadable instant, an
// inverted or over-wide window is corrected or dropped, and the page says so through `issues`.

/** Mirrors the backend's `api_max_range_days` (`app/core/config.py`, default 365). */
export const MAX_RANGE_DAYS = 365;

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

const ALL_CHIPS = [
  { key: "1h", ms: HOUR_MS },
  { key: "24h", ms: DAY_MS },
  { key: "7d", ms: 7 * DAY_MS },
  { key: "30d", ms: 30 * DAY_MS },
] as const;

export type RangeChip = (typeof ALL_CHIPS)[number]["key"];

/** The chips on offer: never one the backend would refuse as wider than its ceiling. */
export const RANGE_CHIPS = ALL_CHIPS.filter((chip) => chip.ms <= MAX_RANGE_DAYS * DAY_MS);

export type RangeIssue =
  | "swapped"
  | "clamped"
  | "unknownRange"
  | "badSince"
  | "badUntil"
  | "chipWins";

export interface RangeState {
  /** The relative chip, or "" when none is active. */
  chip: RangeChip | "";
  /** A Custom window is in the URL (absolute `since` and/or `until`). */
  custom: boolean;
  /** ISO instant sent to the API as `since`, or "". For a chip: now minus the chip. */
  since: string;
  /** ISO instant sent as `until`, or "" (a chip and an open Custom end both mean "now"). */
  until: string;
  issues: RangeIssue[];
}

type Raw = string | string[] | undefined;

function first(value: Raw): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * A URL or `datetime-local` value as an ISO instant, or "" if it is not a real timestamp. A
 * value with no zone designator is read as UTC: left bare, `Date.parse` would use the server's
 * zone, and every label on these pages says UTC.
 */
export function instant(value: Raw): string {
  const text = first(value)?.trim();
  if (!text) return "";
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : `${text}Z`;
  const ms = Date.parse(zoned);
  return Number.isNaN(ms) ? "" : new Date(ms).toISOString();
}

export function parseRange(params: Record<string, Raw>, nowMs: number): RangeState {
  const issues: RangeIssue[] = [];
  const rawRange = first(params.range)?.trim() ?? "";
  const rawSince = first(params.since)?.trim() ?? "";
  const rawUntil = first(params.until)?.trim() ?? "";

  const chip = RANGE_CHIPS.find((c) => c.key === rawRange);
  if (rawRange && !chip) issues.push("unknownRange");
  if (chip) {
    if (rawSince || rawUntil) issues.push("chipWins");
    return {
      chip: chip.key,
      custom: false,
      since: new Date(nowMs - chip.ms).toISOString(),
      until: "",
      issues,
    };
  }

  let since = instant(rawSince);
  let until = instant(rawUntil);
  if (rawSince && !since) issues.push("badSince");
  if (rawUntil && !until) issues.push("badUntil");

  // An inverted window is a slip, not an attack: swapping is what the analyst meant, and
  // sending it on would only earn a 422.
  if (since && until && Date.parse(since) > Date.parse(until)) {
    [since, until] = [until, since];
    issues.push("swapped");
  }
  if (since && until) {
    const max = MAX_RANGE_DAYS * DAY_MS;
    if (Date.parse(until) - Date.parse(since) > max) {
      since = new Date(Date.parse(until) - max).toISOString();
      issues.push("clamped");
    }
  }
  return { chip: "", custom: Boolean(since || until), since, until, issues };
}

/** An instant as the URL writes it: whole seconds, `Z`. */
export function urlInstant(iso: string): string {
  return iso.replace(/\.\d{3}Z$/, "Z");
}

/** The URL keys of a range, canonical: a chip is `range`, a Custom window is `since`/`until`. */
export function rangeQuery(range: RangeState): Record<string, string> {
  if (range.chip) return { range: range.chip };
  const out: Record<string, string> = {};
  if (range.since) out.since = urlInstant(range.since);
  if (range.until) out.until = urlInstant(range.until);
  return out;
}

export interface RangeWindow {
  /** ISO start, or "" when only an end was given. */
  from: string;
  /** ISO end: the request's `until`, or now when none was given. */
  to: string;
  /** The end is the moment the page was read, not a stored bound. */
  toIsNow: boolean;
}

/** The absolute window the page is showing, for the text beside the chips. */
export function rangeWindow(range: RangeState, nowMs: number): RangeWindow | null {
  if (!range.since && !range.until) return null;
  return {
    from: range.since,
    to: range.until || new Date(nowMs).toISOString(),
    toIsNow: !range.until,
  };
}

/**
 * `2026-11-05 19:05 UTC`: whole minutes, the year kept, the zone named. `bare` drops the zone
 * from the first of two stamps that share it ("2026-11-04 19:05 to 2026-11-05 19:05 UTC").
 */
export function stampText(iso: string, bare = false): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}${bare ? "" : " UTC"}`
  );
}
