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

import { useLocale, useTranslations } from "next-intl";

import { useBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator } from "@/i18n/textTranslator";
import { formatShare, type FormatLocale } from "@/lib/format";
import { own } from "@/i18n/ownKey";

import "./Evidence.css";

type Scale = "raw" | "percent";

/** A unit that is a word, by its catalog key (`evidence.unit.<word>`); it follows the figure's count. */
interface Word {
  word: string;
}

interface Measure {
  /**
   * The unit shown after the figure: a symbol both languages share (`EPS`, `z`, `s`) or a word
   * looked up in the catalog.
   */
  unit: string | Word | null;
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
const percent = (unit: string | Word): Measure => ({ unit, scale: "percent" });
const word = (key: string): Word => ({ word: key });

const VOLUME: Spec = {
  value: raw("EPS"),
  threshold: raw("z"),
  deviation: raw("z"),
  samples: raw(word("baselineSample")),
};
const RATIO: Spec = {
  value: percent("%"),
  threshold: percent("%"),
  deviation: percent("pp"),
  samples: raw(word("event")),
};

/** The detector table. Keys are the Phase A `AnomalyType` values. */
const SPECS: Record<string, Spec> = {
  NO_EVENTS: {
    value: raw(word("eventsPerBucket")),
    threshold: raw(word("events")),
    deviation: raw(null),
    samples: raw(word("baselineSample")),
  },
  VOLUME_DROP: VOLUME,
  VOLUME_SPIKE: VOLUME,
  PARSING_DEGRADATION: RATIO,
  UNKNOWN_EVENT_SPIKE: RATIO,
  TIMESTAMP_DELAY: {
    value: raw("s"),
    threshold: raw("s"),
    deviation: raw(word("timesLimit")),
    samples: raw(word("interval")),
  },
  CARDINALITY_DROP: {
    value: raw(word("sourceIps")),
    threshold: raw(word("sourceIps")),
    deviation: percent(word("belowBaseline")),
    samples: raw(word("baselineSample")),
  },
  COLLECTION_ERROR: {
    value: raw(word("error")),
    threshold: raw(word("error")),
    deviation: raw(word("error")),
    samples: raw(word("interval")),
  },
  REPEATED_PAYLOAD: {
    value: raw(word("interval")),
    threshold: raw(word("interval")),
    deviation: raw(word("interval")),
    samples: raw(word("interval")),
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

/**
 * A share is a percentage figure whose unit is the "%" of the shared formatter: the "%" and a
 * word after it ("% below baseline" is the share, then "below baseline"). A plain "pp" is not.
 */
function isShare(measure: Measure | null): measure is Measure {
  return measure !== null && measure.scale === "percent" && measure.unit !== "pp";
}

function format(n: number, locale: FormatLocale): string {
  return n.toLocaleString(locale === "tr" ? "tr-TR" : "en-US", { maximumFractionDigits: 3 });
}

/** The stored `reason_params` object, or null when the evidence carries none (an older row). */
function reasonParams(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function Evidence({
  data,
  omitReason,
}: {
  data: Record<string, unknown>;
  /** When the rendered reason equals this string, the reason line is left out. Figures stay. */
  omitReason?: string | null;
}) {
  const t = useTranslations("evidence");
  const backendMessage = useBackendMessage();
  // Unit and label keys are built from the detector table, so they are looked up by name.
  const lookup = asTextTranslator(t);
  const locale = useLocale() as FormatLocale;
  if (!data || Object.keys(data).length === 0) {
    return <span className="subtitle">{t("none")}</span>;
  }
  const spec = (typeof data.anomaly_type === "string" && own(SPECS, data.anomaly_type)) || NONE;

  const rows: [string, string, Measure | null][] = [
    ["observed", "observed_value", spec.value],
    ["expected", "expected_value", spec.value],
    ["baselineLow", "baseline_low", spec.value],
    ["baselineHigh", "baseline_high", spec.value],
    ["deviation", "deviation_score", spec.deviation],
    ["threshold", "threshold", spec.threshold],
    ["samples", "sample_count", spec.samples],
    // The detector's own 0-to-1 score, stated as such, as the incident page does.
    ["confidence", "confidence", { unit: word("zeroToOne"), scale: "raw" }],
  ];
  const unitText = (unit: Measure["unit"], n: number): string | null => {
    if (unit == null) return null;
    if (typeof unit === "string") return unit;
    return lookup(`unit.${unit.word}`, { count: n });
  };
  const reason =
    typeof data.reason === "string"
      ? backendMessage({
          text: data.reason,
          code: typeof data.reason_code === "string" ? data.reason_code : null,
          params: reasonParams(data.reason_params),
        })
      : null;
  return (
    <div className="evidence">
      {reason != null && reason !== omitReason && (
        <p className="evidence-reason">{reason}</p>
      )}
      <table className="evidence-table" aria-label={t("tableLabel")}>
        <tbody>
          {rows.map(([name, key, measure]) => {
            const n = measure ? figure(data[key], measure.scale) : null;
            const share = n != null && isShare(measure);
            return (
              <tr key={name}>
                <td className="evidence-label">{lookup(`label.${name}`)}</td>
                <td className="num evidence-value">
                  {n == null ? "—" : share ? formatShare(n / 100, locale) : format(n, locale)}
                </td>
                <td className="evidence-unit">
                  {n == null || !measure
                    ? null
                    : share
                      ? typeof measure.unit === "string"
                        ? null
                        : unitText(measure.unit, n)
                      : unitText(measure.unit, n)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
