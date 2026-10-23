// The one-line IPS incident summary.
//
// Deterministic, built only from values the API returned: no model, no
// reconstruction of detection logic, no causation. The server's own `reason`
// is shown separately in the detection block; this sentence restates the
// headline figures in the detector's own unit.

import type { IpsAnomalyDetail } from "@/lib/api";

import { formatPercent } from "@/lib/format";

import { detectorSpec, ipsFigures } from "./ipsDetectors";
import { bucketWidth, formatFigure, unitText } from "./ipsFormat";
import { entityLabel, ipsTypeLabel } from "./ipsLabels";

export interface IpsSummary {
  text: string;
  /** A stated limitation on the figures, or null. */
  caveat: string | null;
}

export function summarizeIpsAnomaly(
  anomaly: IpsAnomalyDetail,
  bucketSeconds: number | null,
): IpsSummary {
  const d = anomaly.detection;
  const type = anomaly.anomaly_type;
  const spec = detectorSpec(type);
  const f = ipsFigures(anomaly, d);
  const lower = d?.observed_measurement === "LOWER_BOUND";
  const m = lower ? "LOWER_BOUND" : null;
  const caveat = lower ? "The observed figure is a lower bound: the true value is at least this." : null;
  const unit = unitText(f.unit, bucketWidth(bucketSeconds));

  // A type this build has no phrasing for: the server's own sentence, or the type
  // as sent. Never a guessed figure and never a Phase A sentence.
  if (!spec) return { text: anomaly.explanation ?? `Flagged by ${type}.`, caveat };

  const observed = formatFigure(f.unit, f.observed, m);
  const against = (text: string) =>
    f.expected == null ? ", with no expected value recorded" : `, against an expected ${text}`;

  if (type === "IPS_NEW_DESTINATION_PORT") {
    return {
      text:
        `A destination port with no verified history carried ${observed} ${unit}; none were ` +
        `expected.`,
      caveat,
    };
  }
  if (spec.unit === "ports") {
    return {
      text:
        `One source IP reached ${observed} distinct destination ports in a bucket` +
        `${against(formatFigure(f.unit, f.expected))}.`,
      caveat,
    };
  }
  if (spec.unit === "share") {
    const counts =
      f.numerator != null && f.denominator != null
        ? ` (${formatFigure("events", f.numerator, m)} of ${formatFigure("events", f.denominator)} events)`
        : "";
    return {
      text:
        `${spec.quantity} was ${formatPercent(f.observed, m)}${counts}` +
        `${against(formatPercent(f.expected))}.`,
      caveat,
    };
  }
  const subject = anomaly.ips_entity ? entityLabel(anomaly.ips_entity) : ipsTypeLabel(type);
  return {
    text: `${subject} reached ${observed} ${unit}${against(`${formatFigure(f.unit, f.expected)} ${unit}`)}.`,
    caveat,
  };
}
