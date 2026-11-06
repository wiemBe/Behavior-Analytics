// The per-row trend of `/behavior/traffic`: one row's events/s per step, drawn as a line.
//
// A null point is a gap: the line breaks there and never drops to zero. Each row is scaled to
// its own lowest and highest point, so the line shows shape; magnitude is in the figure columns.

export const SPARK_W = 120;
export const SPARK_H = 24;
const PAD = 2;

export interface Spark {
  /** Runs of consecutive measured points, each as "x,y x,y …"; a run of one has no line. */
  lines: string[];
  /** A measured point with a gap on both sides: a line cannot show it, a dot does. */
  dots: { x: number; y: number }[];
  peak: { x: number; y: number } | null;
  min: number;
  max: number;
  gaps: number;
  measured: number;
}

/** Null when there is nothing to draw: no measured point. */
export function sparkOf(series: readonly (number | null)[]): Spark | null {
  const values = series.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (values.length === 0) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const n = series.length;
  const x = (i: number) => (n === 1 ? SPARK_W / 2 : PAD + (i * (SPARK_W - 2 * PAD)) / (n - 1));
  // A flat row sits in the middle: its shape is "no change", not "at the bottom".
  const y = (v: number) =>
    max === min ? SPARK_H / 2 : SPARK_H - PAD - ((v - min) / (max - min)) * (SPARK_H - 2 * PAD);
  const lines: string[] = [];
  const dots: { x: number; y: number }[] = [];
  let run: string[] = [];
  let runPoint: { x: number; y: number } | null = null;
  const flush = () => {
    if (run.length > 1) lines.push(run.join(" "));
    else if (runPoint) dots.push(runPoint);
    run = [];
    runPoint = null;
  };
  let peakIndex = -1;
  series.forEach((v, i) => {
    if (typeof v !== "number" || !Number.isFinite(v)) {
      flush();
      return;
    }
    const pt = { x: x(i), y: y(v) };
    run.push(`${pt.x.toFixed(1)},${pt.y.toFixed(1)}`);
    runPoint = pt;
    if (v === max && peakIndex < 0) peakIndex = i;
  });
  flush();
  const gaps = series.length - values.length;
  const peak = max === min ? null : { x: x(peakIndex), y: y(max) };
  return { lines, dots, peak, min, max, gaps, measured: values.length };
}
