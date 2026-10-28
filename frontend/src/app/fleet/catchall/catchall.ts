// Presentation rules for the catch-all senders page.
//
// The rule this file exists to enforce: a bucket with no row was NOT MEASURED.
// It is a gap, never a zero, and a gap is only claimed where it can be known:
// inside the range and before the latest measured bucket. After the latest
// measured bucket nothing is claimed at all; the page says how far collection
// got instead.

import { historyIntervals } from "@/components/behavior/historyOrigin";
import {
  ApiError,
  actionErrorMessage,
  type CatchallBucket,
  type CatchallReviewStatus,
  type CatchallSender,
} from "@/lib/api";
import { englishFleetText, plainCount, type FleetText } from "@/lib/fleetText";
import { formatCount } from "@/lib/format";

export { formatCount };

export const UNKNOWN = "unknown";

// --- figures -------------------------------------------------------------------

/** The 24 h figure of a sender: a lower bound, or `unknown` when no top list named it. */
export function formatEvents24h(value: number | null, text: FleetText = englishFleetText): string {
  return value == null ? text.say("catchall.unknown") : formatCount(value, text.locale);
}

/** English; the catalog (`catchall.events24hNote`) carries both languages. */
export const EVENTS_24H_NOTE =
  "A lower bound: the sum of the stored top-sender lists that name this sender. " +
  "Unknown means no stored top list named it; that is not zero.";

/** `5 min`, `1 h`, `30 s` — the unit on the chart axis. */
export function bucketUnit(seconds: number, text: FleetText = englishFleetText): string {
  const count = (n: number) => plainCount(n, text.locale);
  if (seconds % 3600 === 0) return text.say("units.hour", { count: count(seconds / 3600) });
  if (seconds % 60 === 0) return text.say("units.minute", { count: count(seconds / 60) });
  return text.say("units.second", { count: count(seconds) });
}

// --- review status -------------------------------------------------------------

export type StatusFilter = CatchallReviewStatus | "ALL";

/** English; `statusLabel` reads the catalog. */
export const STATUS_LABELS: Record<CatchallReviewStatus, string> = {
  NEW: "New",
  REVIEWED: "Reviewed",
  DISMISSED: "Dismissed",
};

export const STATUS_FILTERS: StatusFilter[] = ["NEW", "REVIEWED", "DISMISSED", "ALL"];

export function statusLabel(value: CatchallReviewStatus, text: FleetText = englishFleetText): string {
  if (!Object.hasOwn(STATUS_LABELS, value)) return value;
  const key = `catchall.status.${value}`;
  return text.say.has?.(key) ? text.say(key) : STATUS_LABELS[value];
}

export function statusFilterLabel(value: StatusFilter, text: FleetText = englishFleetText): string {
  return value === "ALL" ? text.say("catchall.status.ALL") : statusLabel(value, text);
}

/** Only NEW asks for attention; the other two are decisions already taken. */
export function statusTone(value: CatchallReviewStatus): "warn" | "" {
  return value === "NEW" ? "warn" : "";
}

/** The permission both actions need. */
export const WRITE_PERMISSION = "log_source:write";

/** English; `deniedNote` reads the catalog. */
export const DENIED_NOTE =
  `Review and Dismiss need the permission ${WRITE_PERMISSION}, which your role does not have. ` +
  "The queue is read-only for you.";

export function deniedNote(text: FleetText = englishFleetText): string {
  return text.say("catchall.deniedNote", { permission: WRITE_PERMISSION });
}

/** Why an action did not change the sender. Always says it did not. */
export function senderFailure(
  err: unknown,
  text: FleetText = englishFleetText,
): { kind: "forbidden" | "error"; text: string } {
  const { say } = text;
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", text: say("catchall.failure.forbidden", { permission: WRITE_PERMISSION }) };
  }
  if (err instanceof ApiError && err.status === 404) {
    return { kind: "error", text: say("catchall.failure.notFound") };
  }
  return {
    kind: "error",
    text: say("catchall.failure.notChanged", { reason: actionErrorMessage(err, say) }),
  };
}

export function reviewedBy(sender: CatchallSender): string | null {
  if (sender.review_status === "NEW" || sender.reviewed_by == null) return null;
  return sender.reviewed_by;
}

/** Why the queue is empty, so a clear queue is never confused with no data. */
export type EmptyKind = "never-collected" | "filtered" | "clear" | "none-in-status";

export function emptyKind(args: {
  filtered: boolean;
  status: StatusFilter;
  anyMeasured: boolean;
}): EmptyKind {
  // Nothing measured anywhere: an empty list says nothing about the senders.
  if (!args.anyMeasured) return "never-collected";
  if (args.filtered) return "filtered";
  return args.status === "NEW" ? "clear" : "none-in-status";
}

// --- bucket slots --------------------------------------------------------------

export type SlotState = "exact" | "lower_bound" | "gap" | "beyond";

export interface Slot {
  /** Bucket start, epoch ms. */
  t: number;
  state: SlotState;
  bucket: CatchallBucket | null;
}

export interface SlotSeries {
  slots: Slot[];
  /** Null when the range holds no measured bucket: the width is then unknown. */
  bucketSeconds: number | null;
  latestStart: number | null;
  gapCount: number;
  lowerBoundCount: number;
}

/**
 * The bucket grid of a range, with every slot classified.
 *
 * Only a slot inside the range and before the latest measured bucket is a gap.
 * A slot after it is `beyond`: collection had not reached it, so it is neither
 * measured nor claimed missing. A row whose measurement is FAILED or
 * NOT_COLLECTED is a gap too, never its stored counts.
 */
export function buildSlots(buckets: CatchallBucket[], sinceMs: number, untilMs: number): SlotSeries {
  const measured = buckets.filter(
    (b) => b.measurement === "EXACT" || b.measurement === "LOWER_BOUND",
  );
  const first = buckets[0];
  if (first === undefined) {
    return { slots: [], bucketSeconds: null, latestStart: null, gapCount: 0, lowerBoundCount: 0 };
  }
  const step = first.bucket_seconds * 1000;
  const anchor = Date.parse(first.bucket_start);
  const byTime = new Map<number, CatchallBucket>();
  for (const b of buckets) byTime.set(Date.parse(b.bucket_start), b);

  const times = new Set<number>(byTime.keys());
  const k0 = Math.ceil((sinceMs - anchor) / step);
  for (let t = anchor + k0 * step; t < untilMs; t += step) times.add(t);

  const latestStart = measured.length
    ? Math.max(...measured.map((b) => Date.parse(b.bucket_start)))
    : null;

  let gapCount = 0;
  let lowerBoundCount = 0;
  const slots: Slot[] = [...times]
    .sort((a, b) => a - b)
    .map((t) => {
      const row = byTime.get(t) ?? null;
      if (row && row.measurement === "EXACT") return { t, state: "exact" as const, bucket: row };
      if (row && row.measurement === "LOWER_BOUND") {
        lowerBoundCount += 1;
        return { t, state: "lower_bound" as const, bucket: row };
      }
      if (latestStart !== null && t < latestStart) {
        gapCount += 1;
        return { t, state: "gap" as const, bucket: null };
      }
      return { t, state: "beyond" as const, bucket: null };
    });
  return { slots, bucketSeconds: first.bucket_seconds, latestStart, gapCount, lowerBoundCount };
}

/**
 * The shaded "not measured" series: the axis maximum at each gap slot, null
 * elsewhere. These are the shaded ranges the chart receives.
 */
export function gapBand(slots: Slot[], max: number): Array<number | null> {
  return slots.map((s) => (s.state === "gap" ? max : null));
}

/**
 * The spans of buckets read from QRadar's past, as the first and last category of each span
 * (the chart's categories are the slot times). Contiguous history buckets are one span; a live
 * bucket, an unmeasured slot or a gap between two history buckets splits it.
 */
export function historySpans(slots: Slot[]): Array<[string, string]> {
  const rows = slots.flatMap((s) => (s.bucket ? [s.bucket] : []));
  const width = (rows[0]?.bucket_seconds ?? 0) * 1000;
  return historyIntervals(rows).map(
    (span): [string, string] => [String(span.start), String(span.end - width)],
  );
}

/** How many measured buckets of the range were read from QRadar's past. */
export function historyCount(slots: Slot[]): number {
  return slots.filter((s) => s.bucket?.origin === "HISTORY").length;
}

/** A round axis maximum at or above `value`, so the shading and the axis agree. */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const pow = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((m) => m >= value) ?? 10 * pow;
  return step;
}

/** What a sender is called where it may wrap: after `.` and `-`, never mid-word. */
export function breakParts(identifier: string): string[] {
  return identifier.split(/(?<=[.\-])/);
}

/** What the bar plots: the events of a measured bucket, null for everything else. */
export function barValue(slot: Slot): number | null {
  return slot.bucket && (slot.state === "exact" || slot.state === "lower_bound")
    ? slot.bucket.events
    : null;
}

/** Index of the nearest measured slot from `from`, in a direction; null at the end. */
export function stepMeasured(slots: Slot[], from: number, direction: 1 | -1): number | null {
  for (let i = from + direction; i >= 0 && i < slots.length; i += direction) {
    if (slots[i].bucket) return i;
  }
  return null;
}

export function latestMeasuredIndex(slots: Slot[]): number | null {
  for (let i = slots.length - 1; i >= 0; i -= 1) if (slots[i].bucket) return i;
  return null;
}

/** The end of the latest measured bucket: how far collection got. */
export function collectedUpTo(series: SlotSeries): number | null {
  if (series.latestStart === null || series.bucketSeconds === null) return null;
  return series.latestStart + series.bucketSeconds * 1000;
}

/** A tick label in UTC: the time, or the date at midnight. */
export function axisLabel(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d.getUTCHours() === 0 && d.getUTCMinutes() === 0) {
    return `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  }
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

export interface RangeOption {
  key: "6h" | "24h" | "7d";
  /** English; `rangeLabel` reads the catalog. */
  label: string;
  ms: number;
}

export function rangeLabel(range: RangeOption, text: FleetText = englishFleetText): string {
  return text.say(`catchall.range.${range.key}`);
}

/** The longest range is exactly what the API accepts (7 days). */
export const RANGES: RangeOption[] = [
  { key: "6h", label: "6 hours", ms: 6 * 3600_000 },
  { key: "24h", label: "24 hours", ms: 24 * 3600_000 },
  { key: "7d", label: "7 days", ms: 7 * 24 * 3600_000 },
];

// --- the admission counts, in words ---------------------------------------------

export function admissionSentences(b: CatchallBucket, text: FleetText = englishFleetText): string[] {
  const { say } = text;
  return [
    say("catchall.admission.admitted", { count: b.admitted }),
    say("catchall.admission.below", { count: b.below_admission_threshold }),
    say("catchall.admission.rejected", { count: b.rejected_over_quota }),
    say("catchall.admission.untracked", {
      senders: b.untracked_senders,
      events: b.untracked_events,
    }),
  ];
}

/** English; `catchall.admission.refusedNote` carries both languages. */
export const REFUSED_NOTE =
  "Refused and invalid senders are counted and never stored by name, so they cannot appear in the top senders.";

/** The explanation of a measurement the pill alone does not carry; null for EXACT. */
export function measurementText(b: CatchallBucket, text: FleetText = englishFleetText): string | null {
  switch (b.measurement) {
    case "EXACT":
      return null;
    case "LOWER_BOUND":
      return text.say("catchall.measurement.lowerBound");
    case "FAILED":
      return text.say("catchall.measurement.failed");
    default:
      return text.say("catchall.measurement.notMeasured");
  }
}
