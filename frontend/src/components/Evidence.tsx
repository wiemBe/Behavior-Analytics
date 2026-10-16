// Renders structured anomaly/alert evidence, not just a label. The backend
// attaches observed vs expected, the baseline band, deviation, threshold,
// sample count and a human reason to every anomaly; this surfaces them.
//
// Every figure carries the unit its detector measures it in. The unit comes from
// the evidence's own `anomaly_type` (app/anomaly/detectors.py): the same field is
// EPS for a volume detector, a share for a parse-ratio detector and seconds for a
// delay detector. A payload with no type this table knows (a scheduled-search
// alert, a future detector) shows its numbers bare rather than under a guessed
// unit, and a missing value is a dash with no unit after it.

import "./Evidence.css";

type Scale = "raw" | "percent";

interface Measure {
  /** The unit shown after the figure; a [singular, plural] pair follows the figure. */
  unit: string | [string, string] | null;
  /** A share is stored 0 to 1 and shown as a percentage. */
  scale: Scale;
}

interface Spec {
  value: Measure;
  threshold: Measure;
  deviation: Measure;
  samples: Measure;
}

const raw = (unit: Measure["unit"]): Measure => ({ unit, scale: "raw" });
const percent = (unit: string): Measure => ({ unit, scale: "percent" });

const VOLUME: Spec = {
  value: raw("EPS"),
  threshold: raw("z"),
  deviation: raw("z"),
  samples: raw(["baseline sample", "baseline samples"]),
};
const RATIO: Spec = {
  value: percent("%"),
  threshold: percent("%"),
  deviation: percent("pp"),
  samples: raw(["event", "events"]),
};

/** The detector table. Keys are the Phase A `AnomalyType` values. */
const SPECS: Record<string, Spec> = {
  NO_EVENTS: {
    value: raw("events / bucket"),
    threshold: raw("events"),
    deviation: raw(null),
    samples: raw(["baseline sample", "baseline samples"]),
  },
  VOLUME_DROP: VOLUME,
  VOLUME_SPIKE: VOLUME,
  PARSING_DEGRADATION: RATIO,
  UNKNOWN_EVENT_SPIKE: RATIO,
  TIMESTAMP_DELAY: {
    value: raw("s"),
    threshold: raw("s"),
    deviation: raw("× limit"),
    samples: raw(["interval", "intervals"]),
  },
  CARDINALITY_DROP: {
    value: raw("source IPs"),
    threshold: raw("source IPs"),
    deviation: percent("% below baseline"),
    samples: raw(["baseline sample", "baseline samples"]),
  },
  COLLECTION_ERROR: {
    value: raw(["error", "errors"]),
    threshold: raw(["error", "errors"]),
    deviation: raw(["error", "errors"]),
    samples: raw(["interval", "intervals"]),
  },
  REPEATED_PAYLOAD: {
    value: raw(["interval", "intervals"]),
    threshold: raw(["interval", "intervals"]),
    deviation: raw(["interval", "intervals"]),
    samples: raw(["interval", "intervals"]),
  },
};

const NONE: Spec = {
  value: raw(null),
  threshold: raw(null),
  deviation: raw(null),
  samples: raw(null),
};

function figure(value: unknown, scale: Scale): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return scale === "percent" ? value * 100 : value;
}

function format(n: number): string {
  return n.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

function unitFor(unit: Measure["unit"], n: number): string | null {
  if (unit == null) return null;
  if (typeof unit === "string") return unit;
  return n === 1 ? unit[0] : unit[1];
}

export function Evidence({ data }: { data: Record<string, unknown> }) {
  if (!data || Object.keys(data).length === 0) {
    return <span className="subtitle">no evidence captured</span>;
  }
  const spec = (typeof data.anomaly_type === "string" && SPECS[data.anomaly_type]) || NONE;

  const rows: [string, string, Measure | null][] = [
    ["Observed", "observed_value", spec.value],
    ["Expected", "expected_value", spec.value],
    ["Baseline low", "baseline_low", spec.value],
    ["Baseline high", "baseline_high", spec.value],
    ["Deviation", "deviation_score", spec.deviation],
    ["Threshold", "threshold", spec.threshold],
    ["Samples", "sample_count", spec.samples],
    // The detector's own 0-to-1 score, stated as such, as the incident page does.
    ["Confidence", "confidence", { unit: "(0 to 1)", scale: "raw" }],
  ];
  return (
    <div className="evidence">
      {typeof data.reason === "string" && <p className="evidence-reason">{data.reason}</p>}
      <table className="evidence-table" aria-label="Detector evidence metrics">
        <tbody>
          {rows.map(([label, key, measure]) => {
            const n = measure ? figure(data[key], measure.scale) : null;
            return (
              <tr key={label}>
                <td className="evidence-label">{label}</td>
                <td className="num evidence-value">{n == null ? "—" : format(n)}</td>
                <td className="evidence-unit">
                  {n != null && measure ? unitFor(measure.unit, n) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
