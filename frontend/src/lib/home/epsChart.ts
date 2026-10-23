// The fleet EPS line (UR.4.3) as a model the chart draws and the text repeats.
//
// A step's `eps` is null unless every in-scope cell was COMPLETE, so a null step is
// not measured: it is a gap in the line and a shaded interval, never a zero and
// never a connected segment (guidelines §4). `expected_eps` is a sum of per-source
// medians, null unless every in-scope source has a reliable cell; it is one line,
// never a band, because a sum of medians is not the median of a sum.
//
// "Measured" is counted from the steps themselves, not from the response's
// coverage field, so the qualifier can never disagree with what is drawn.

import type { FleetEpsSeries } from "@/lib/api";
import { formatCount } from "@/lib/format";

import { notCollected, partial, type Qualifier } from "./qualifiers";

export interface EpsStep {
  /** Epoch ms of the step start. */
  t: number;
  eps: number | null;
  expected: number | null;
  /** Source-intervals complete / expected in this step. */
  cellsComplete: number;
  cellsExpected: number;
}

/** A run of consecutive unmeasured steps, as a time interval. */
export interface EpsGap {
  start: number;
  end: number;
}

export interface EpsChartModel {
  steps: EpsStep[];
  gaps: EpsGap[];
  /** Indices of measured steps with an unmeasured step on both sides. */
  lone: number[];
  measured: number;
  total: number;
  hasExpected: boolean;
  /** Largest measured value; null when nothing was measured. */
  peak: number | null;
  stepSeconds: number;
  qualifiers: Qualifier[];
}

const finite = (v: number | null | undefined): v is number => v != null && Number.isFinite(v);

export function epsChartModel(series: FleetEpsSeries): EpsChartModel {
  const points = series.series[0]?.points ?? [];
  const stepMs = series.step_seconds * 1000;
  const steps: EpsStep[] = points.map((p) => ({
    t: Date.parse(p.step_start),
    eps: finite(p.eps) && p.eps >= 0 ? p.eps : null,
    expected: finite(p.expected_eps) && p.expected_eps >= 0 ? p.expected_eps : null,
    cellsComplete: p.cells_complete,
    cellsExpected: p.cells_expected,
  }));

  const gaps: EpsGap[] = [];
  let open: number | null = null;
  steps.forEach((s, i) => {
    if (s.eps === null) {
      if (open === null) open = i;
    } else if (open !== null) {
      gaps.push({ start: steps[open].t, end: steps[i].t });
      open = null;
    }
  });
  if (open !== null) {
    // The window ends on a step boundary: the last unmeasured step covers one step.
    gaps.push({ start: steps[open].t, end: steps[steps.length - 1].t + stepMs });
  }

  const lone = steps.flatMap((s, i) =>
    s.eps !== null && steps[i - 1]?.eps == null && steps[i + 1]?.eps == null ? [i] : [],
  );
  const values = steps.flatMap((s) => (s.eps === null ? [] : [s.eps]));
  const measured = values.length;
  const total = steps.length;

  const qualifiers: Qualifier[] = [];
  if (total === 0) {
    qualifiers.push(notCollected("The window holds no step."));
  } else if (measured === 0) {
    qualifiers.push(
      notCollected(
        `No step of ${total.toLocaleString("en-US")} has every in-scope source-interval complete.`,
      ),
    );
  } else if (measured < total) {
    qualifiers.push(partial(measured, total, "steps"));
  }

  return {
    steps,
    gaps,
    lone,
    measured,
    total,
    hasExpected: steps.some((s) => s.expected !== null),
    peak: measured ? Math.max(...values) : null,
    stepSeconds: series.step_seconds,
    qualifiers,
  };
}

/** The tooltip line for an unmeasured step: how much of it was measured. */
export function gapText(step: Pick<EpsStep, "cellsComplete" | "cellsExpected">): string {
  const n = (v: number) => formatCount(v);
  return `${n(step.cellsComplete)} of ${n(step.cellsExpected)} source-intervals measured`;
}

const WINDOW_TEXT: Record<FleetEpsSeries["window"], string> = {
  "1h": "1 h",
  "24h": "24 h",
  "7d": "7 d",
};

export const windowText = (w: FleetEpsSeries["window"]) => WINDOW_TEXT[w];

/** What the chart shows, as prose for assistive technology. */
export function epsSummaryText(series: FleetEpsSeries, model: EpsChartModel): string {
  const n = (v: number) => formatCount(v);
  const head = `Fleet EPS over ${WINDOW_TEXT[series.window]}, times in UTC.`;
  if (model.measured === 0) {
    return `${head} No step was measured, so no line is drawn.`;
  }
  const peak = (model.peak as number).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const expected = model.hasExpected
    ? " The dashed line is the sum of per-source expected EPS, drawn only where every source has a reliable baseline. It is not a range."
    : " No expected line: not every source has a reliable baseline.";
  const gaps =
    model.measured < model.total
      ? ` Shaded intervals were not measured; they are not zero traffic.`
      : "";
  return `${head} ${n(model.measured)} of ${n(model.total)} steps measured, peak ${peak} EPS.${expected}${gaps}`;
}
