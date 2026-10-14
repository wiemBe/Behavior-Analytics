// The path of a sparkline (UR.4.2 card 1), as pure geometry.
//
// A null step is a gap: the path breaks there and starts a new subpath after it.
// A line drawn across a step that was not measured would turn a collector gap
// into a steady rate, and a null drawn as 0 would read as a drop (guidelines §4).
// A measured step with a gap on both sides is its own one-point subpath (`M x y L x y`),
// which a round line cap draws as a dot, so a lone measurement is not lost.
//
// The y domain is 0 to the largest measured value, so the line's height is
// honest about proportion. The sparkline has no axis; its figures are in text.

export interface SparklineGeometry {
  /** SVG path data; empty when no step was measured. */
  path: string;
  /** Contiguous measured runs, i.e. subpaths. */
  runs: number;
  measured: number;
  /** Largest measured value; null when none was. */
  max: number | null;
}

const usable = (v: number | null | undefined): v is number =>
  v != null && Number.isFinite(v) && v >= 0;

const fmt = (n: number) => String(Math.round(n * 100) / 100);

export function sparkline(
  values: (number | null)[],
  width: number,
  height: number,
  pad = 1,
): SparklineGeometry {
  const measuredValues = values.filter(usable);
  const max = measuredValues.length ? Math.max(...measuredValues) : null;
  const x = (i: number) => (values.length > 1 ? (i * width) / (values.length - 1) : width / 2);
  const y = (v: number) =>
    max === null || max <= 0 ? height - pad : pad + (height - 2 * pad) * (1 - v / max);

  const parts: string[] = [];
  let runs = 0;
  let open = false;
  let last: [number, number] | null = null;
  values.forEach((v, i) => {
    if (!usable(v)) {
      if (open && last) {
        // A one-point run: close it as a zero-length segment.
        if (parts[parts.length - 1].startsWith("M")) parts.push(`L${fmt(last[0])} ${fmt(last[1])}`);
      }
      open = false;
      last = null;
      return;
    }
    const px = x(i);
    const py = y(v);
    parts.push(`${open ? "L" : "M"}${fmt(px)} ${fmt(py)}`);
    if (!open) runs += 1;
    open = true;
    last = [px, py];
  });
  if (open && last && parts[parts.length - 1].startsWith("M")) {
    parts.push(`L${fmt(last[0])} ${fmt(last[1])}`);
  }
  return { path: parts.join(" "), runs, measured: measuredValues.length, max };
}
