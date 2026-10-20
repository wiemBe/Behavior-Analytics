// Which steps of the fleet EPS series hold cells read from QRadar's past (HB-14's
// `cells_from_history`, HISTORY-BACKFILL §B.10.3 item 5). A step is a sum over many source
// cells, so it can be wholly history, partly history or live; the tooltip says "n of m cells",
// and the chart shades only the steps that are wholly history, in the neutral span the other
// history charts use. A zero (or an absent field) adds nothing.

import type { FleetEpsSeries } from "@/lib/api";
import {
  HISTORY_TEXT,
  type HistoryInterval,
} from "@/components/behavior/historyOrigin";

export interface EpsHistoryStep {
  /** Epoch ms of the step start. */
  t: number;
  /** Of the complete cells, how many were read from history. */
  fromHistory: number;
  complete: number;
}

export interface EpsHistory {
  steps: Map<number, EpsHistoryStep>;
  /** Contiguous steps that are wholly history. */
  spans: HistoryInterval[];
  /** Steps with at least one history cell. */
  partlyOrWholly: number;
  /** Whether the chart has room for the words inside it (the expanded widget, not the tile). */
  labelled: boolean;
}

export function epsHistory(series: FleetEpsSeries, labelled = true): EpsHistory {
  const points = series.series[0]?.points ?? [];
  const stepMs = series.step_seconds * 1000;
  const steps = new Map<number, EpsHistoryStep>();
  const spans: HistoryInterval[] = [];
  let partlyOrWholly = 0;
  for (const p of points) {
    const h = p.cells_from_history ?? 0;
    if (h <= 0) continue;
    const t = Date.parse(p.step_start);
    steps.set(t, { t, fromHistory: h, complete: p.cells_complete });
    partlyOrWholly += 1;
    // Wholly history: every complete cell was read from the past, and the step was measured.
    if (p.eps !== null && h === p.cells_complete) {
      const last = spans[spans.length - 1];
      if (last && t <= last.end) {
        last.end = t + stepMs;
        last.buckets += 1;
      } else {
        spans.push({ start: t, end: t + stepMs, buckets: 1 });
      }
    }
  }
  return { steps, spans, partlyOrWholly, labelled };
}

/** The tooltip line of one step; empty when no cell of it came from history. */
export function epsHistoryLine(history: EpsHistory | undefined, t: number): string {
  const s = history?.steps.get(t);
  if (!s) return "";
  const n = (v: number) => v.toLocaleString("en-US");
  return `${n(s.fromHistory)} of ${n(s.complete)} cells read from QRadar history`;
}

/** The same statement for assistive technology. */
export function epsHistorySummary(history: EpsHistory | undefined): string {
  if (!history || history.partlyOrWholly === 0) return "";
  const n = history.partlyOrWholly.toLocaleString("en-US");
  const whole = history.spans.reduce((a, s) => a + s.buckets, 0);
  const wholly =
    whole > 0
      ? ` ${whole.toLocaleString("en-US")} of them ${whole === 1 ? "is" : "are"} wholly ${HISTORY_TEXT} and shaded.`
      : "";
  return `${n} ${history.partlyOrWholly === 1 ? "step holds" : "steps hold"} cells ${HISTORY_TEXT}.${wholly}`;
}
