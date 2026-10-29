// Which steps of the fleet EPS series hold cells read from QRadar's past (HB-14's
// `cells_from_history`, HISTORY-BACKFILL §B.10.3 item 5). A step is a sum over many source
// cells, so it can be wholly history, partly history or live; the tooltip says "n of m cells",
// and the chart shades only the steps that are wholly history, in the neutral span the other
// history charts use. A zero (or an absent field) adds nothing.

import type { FleetEpsSeries } from "@/lib/api";
import type { HistoryInterval } from "@/components/behavior/historyOrigin";
import { type TextTranslator } from "@/i18n/textTranslator";
import { englishText } from "@/widgets/englishText";
import { formatCount, type FormatLocale } from "@/lib/format";

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
export function epsHistoryLine(
  history: EpsHistory | undefined,
  t: number,
  say: TextTranslator = englishText,
  locale: FormatLocale = "en",
): string {
  const s = history?.steps.get(t);
  if (!s) return "";
  return say("home.history.cells", {
    from: formatCount(s.fromHistory, locale),
    complete: formatCount(s.complete, locale),
  });
}

/** The same statement for assistive technology. */
export function epsHistorySummary(
  history: EpsHistory | undefined,
  say: TextTranslator = englishText,
): string {
  if (!history || history.partlyOrWholly === 0) return "";
  const whole = history.spans.reduce((a, s) => a + s.buckets, 0);
  const wholly = whole > 0 ? ` ${say("home.history.wholly", { count: whole })}` : "";
  return `${say("home.history.holds", { count: history.partlyOrWholly })}${wholly}`;
}
