// The one-line IPS incident summary.
//
// Deterministic, built only from values the API returned: no model, no
// reconstruction of detection logic, no causation. The server's own `reason`
// is shown separately in the detection block; this sentence restates the
// headline figures in the detector's own unit.

import type { IpsAnomalyDetail } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { formatPercent } from "@/lib/format";

import { detectorSpec, ipsFigures, quantityText } from "./ipsDetectors";
import { ENGLISH_IPS } from "./ipsI18n";
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
  i18n: BehaviorI18n = ENGLISH_IPS,
): IpsSummary {
  const d = anomaly.detection;
  const type = anomaly.anomaly_type;
  const spec = detectorSpec(type);
  const f = ipsFigures(anomaly, d);
  const lower = d?.observed_measurement === "LOWER_BOUND";
  const m = lower ? "LOWER_BOUND" : null;
  const caveat = lower ? i18n.t("ips.summary.lowerBound") : null;
  const unit = unitText(f.unit, bucketWidth(bucketSeconds), i18n);
  const say = (key: string, values: Record<string, string>) => i18n.t(`ips.summary.${key}`, values);
  const against = (text: string) =>
    f.expected == null ? say("noExpected", {}) : say("against", { value: text });

  // A type this build has no phrasing for: the server's own sentence, or the type
  // as sent. Never a guessed figure and never a Phase A sentence.
  if (!spec) return { text: anomaly.explanation ?? say("flaggedBy", { type }), caveat };

  const observed = formatFigure(f.unit, f.observed, m, i18n);

  if (type === "IPS_NEW_DESTINATION_PORT") {
    return {
      text: say("newPort", { observed, unit }),
      caveat,
    };
  }
  if (spec.unit === "ports") {
    return {
      text: say("fanout", {
        observed,
        against: against(formatFigure(f.unit, f.expected, undefined, i18n)),
      }),
      caveat,
    };
  }
  if (spec.unit === "share") {
    const counts =
      f.numerator != null && f.denominator != null
        ? say("counts", {
            num: formatFigure("events", f.numerator, m, i18n),
            den: formatFigure("events", f.denominator, undefined, i18n),
          })
        : "";
    return {
      text:
        say("share", {
          quantity: quantityText(type, i18n),
          share: formatPercent(f.observed, m, 1, i18n.locale),
          counts,
          against: against(formatPercent(f.expected, undefined, 1, i18n.locale)),
        }),
      caveat,
    };
  }
  const subject = anomaly.ips_entity
    ? entityLabel(anomaly.ips_entity, i18n)
    : ipsTypeLabel(type, i18n);
  return {
    text: say("events", {
      subject,
      observed,
      unit,
      against: against(`${formatFigure(f.unit, f.expected, undefined, i18n)} ${unit}`),
    }),
    caveat,
  };
}
