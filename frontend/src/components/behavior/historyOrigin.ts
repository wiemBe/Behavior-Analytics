// Which buckets of a series were read from QRadar's past (HISTORY-BACKFILL §B.10.3 item 5).
//
// The history backfill writes buckets for learning only; the engine never judges them. Every
// family API says which bucket is which (`origin`, HB-14), and the charts and tables of every
// family label them with the same words. This module owns those words and the arithmetic, so
// no chart words it differently:
//
//   * `HISTORY` is shaded (contiguous history buckets are one span) and labelled in text.
//   * `null` is unknown: the source's bucket record is missing. It is never drawn as live and
//     never shaded as history; the tooltip and the text alternative say "origin unknown".
//   * `LIVE`, and a response that carries no `origin` at all, say nothing: a series with no
//     history bucket looks exactly as it did before.

import type { TextTranslator } from "@/i18n/textTranslator";
import type { HistoryOrigin } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import type { FormatLocale } from "@/lib/format";

export const HISTORY_TEXT = "read from QRadar history, not judged";
export const HISTORY_TEXT_CAPITAL = "Read from QRadar history, not judged";
export const ORIGIN_UNKNOWN_TEXT = "origin unknown";

export interface OriginRow {
  bucket_start: string;
  bucket_seconds: number;
  origin?: HistoryOrigin | null;
}

export interface HistoryInterval {
  /** Epoch ms of the first history bucket's start. */
  start: number;
  /** Epoch ms of the last history bucket's end. */
  end: number;
  buckets: number;
}

export interface OriginCounts {
  total: number;
  history: number;
  unknown: number;
}

const byTime = <T extends { bucket_start: string }>(rows: T[]): T[] =>
  [...rows].sort((a, b) => Date.parse(a.bucket_start) - Date.parse(b.bucket_start));

export function originCounts(rows: OriginRow[]): OriginCounts {
  return {
    total: rows.length,
    history: rows.filter((r) => r.origin === "HISTORY").length,
    unknown: rows.filter((r) => r.origin === null).length,
  };
}

/** Contiguous HISTORY buckets as spans; a live or unknown bucket between two ends a span. */
export function historyIntervals(rows: OriginRow[]): HistoryInterval[] {
  const out: HistoryInterval[] = [];
  for (const r of byTime(rows)) {
    if (r.origin !== "HISTORY") continue;
    const start = Date.parse(r.bucket_start);
    const end = start + r.bucket_seconds * 1000;
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
    const last = out[out.length - 1];
    if (last && start <= last.end) {
      last.end = Math.max(last.end, end);
      last.buckets += 1;
    } else {
      out.push({ start, end, buckets: 1 });
    }
  }
  return out;
}

/** Bucket start (epoch ms) to the bucket's origin, for a tooltip keyed by the point's time. */
export function originByTime(rows: OriginRow[]): Map<number, HistoryOrigin | null | undefined> {
  return new Map(rows.map((r) => [Date.parse(r.bucket_start), r.origin]));
}

/**
 * The words of the interface language for the history statements: the translator over the full
 * catalog (`behavior.history.*`). Without one every function here keeps its English, so the
 * families whose pages are not converted yet are unchanged.
 */
export interface HistoryWords {
  say: TextTranslator;
  locale: FormatLocale;
}

/** The tooltip / table line for one bucket; empty when there is nothing to say. */
export function originText(origin: HistoryOrigin | null | undefined, words?: HistoryWords): string {
  if (origin === "HISTORY") return words ? words.say("behavior.history.textCapital") : HISTORY_TEXT_CAPITAL;
  if (origin === null) {
    return words
      ? words.say("behavior.history.originLine")
      : `Origin: ${ORIGIN_UNKNOWN_TEXT} (the source's bucket record is missing)`;
  }
  return "";
}

/** A marker for a table row: short, in words. */
export function originMarker(origin: HistoryOrigin | null | undefined, words?: HistoryWords): string {
  if (origin === "HISTORY") return words ? words.say("behavior.history.text") : HISTORY_TEXT;
  if (origin === null) return words ? words.say("behavior.history.originUnknown") : ORIGIN_UNKNOWN_TEXT;
  return "";
}

const utc = (ms: number) => {
  const d = new Date(ms).toISOString();
  return `${d.slice(0, 10)} ${d.slice(11, 16)}`;
};

/** The same statement for assistive technology: counts, then each span with its zone. */
export function historySummary(rows: OriginRow[], words?: HistoryWords): string {
  const c = originCounts(rows);
  const parts: string[] = [];
  if (c.history > 0) {
    const spans = historyIntervals(rows)
      .map((s) =>
        words
          ? words.say("behavior.history.span", { start: utc(s.start), end: utc(s.end) })
          : `${utc(s.start)} to ${utc(s.end)} UTC`,
      )
      .join("; ");
    parts.push(
      words
        ? words.say("behavior.history.summaryHistory", {
            count: c.total,
            history: plainCount(c.history, words.locale),
            total: plainCount(c.total, words.locale),
            spans,
          })
        : `${c.history} of ${c.total} ${c.total === 1 ? "bucket was" : "buckets were"} ${HISTORY_TEXT} (${spans}).`,
    );
  }
  if (c.unknown > 0) {
    parts.push(
      words
        ? words.say("behavior.history.summaryUnknown", {
            count: c.unknown,
            unknown: plainCount(c.unknown, words.locale),
          })
        : `${c.unknown} ${c.unknown === 1 ? "bucket has" : "buckets have"} an ${ORIGIN_UNKNOWN_TEXT}: the source's bucket record is missing.`,
    );
  }
  return parts.join(" ");
}

export interface HistoryShade {
  color: string;
  border: string;
  text: string;
  font: string;
}

/** The neutral fill of the history span, resolved from the tokens (browser only). */
export function readHistoryShade(root: Element = document.documentElement): HistoryShade {
  const style = getComputedStyle(root);
  return {
    color: style.getPropertyValue("--neutral-bg").trim(),
    border: style.getPropertyValue("--neutral-border").trim(),
    text: style.getPropertyValue("--text-2").trim(),
    font: style.getPropertyValue("--font").trim(),
  };
}

export type HistoryMarkArea<X extends number | string = number> = [
  {
    xAxis: X;
    name: string;
    itemStyle: { color: string; borderColor: string; borderWidth: number; borderType: "dashed" };
    label?: {
      show: boolean;
      position?: "insideTopLeft";
      formatter?: string;
      color?: string;
      fontFamily?: string;
      fontSize?: number;
    };
  },
  { xAxis: X },
];

/** The same spans with no words inside, for a second panel that shares the first's time axis. */
export function withoutLabels(areas: HistoryMarkArea[]): HistoryMarkArea[] {
  return areas.map(([from, to]): HistoryMarkArea => [{ ...from, label: { show: false } }, to]);
}

/**
 * The spans in the shape of the other incident spans' `markArea` data: the neutral fill with a
 * dashed outline (the band of the expected range is a neutral fill too, and has no outline).
 * The widest span carries the words inside the chart, unless `labelled` is false (a chart too
 * small to hold them clear of the data: the Dashboard widgets); the key and the text alternative carry
 * them for every span, so several narrow spans never overprint one another.
 */
export function historyMarkAreas(
  intervals: HistoryInterval[],
  shade: HistoryShade,
  labelled = true,
  text: string = HISTORY_TEXT,
): HistoryMarkArea[] {
  const widest = intervals.reduce(
    (best, s, i) => (s.end - s.start > (intervals[best]?.end ?? 0) - (intervals[best]?.start ?? 0) ? i : best),
    0,
  );
  return intervals.map((s, i) => historyMarkArea(s.start, s.end, shade, labelled && i === widest, text));
}

/** One span between two axis values (epoch ms on a time axis, a category on a category axis). */
export function historyMarkArea<X extends number | string>(
  from: X,
  to: X,
  shade: HistoryShade,
  labelled: boolean,
  text: string = HISTORY_TEXT,
): HistoryMarkArea<X> {
  return [
    {
      xAxis: from,
      name: text,
      itemStyle: {
        color: shade.color,
        borderColor: shade.border,
        borderWidth: 1,
        borderType: "dashed",
      },
      ...(labelled
        ? {
            label: {
              show: true,
              position: "insideTopLeft" as const,
              formatter: text,
              color: shade.text,
              fontFamily: shade.font,
              fontSize: 12,
            },
          }
        : {}),
    },
    { xAxis: to },
  ];
}
