import Link from "next/link";
import { useTranslations } from "next-intl";
// A source's anomalies, split by whether they still need work.
//
// The split is derived from the lifecycle state, never from `resolved_at IS
// NULL`. A NORMAL row with no resolved timestamp is not an active incident,
// and reading the absence of a timestamp as evidence of activity is the defect
// the backend corrected in ec5d352 — the UI must not reintroduce it.

import { TableScroll } from "@/components/ui/TableScroll";
import { useEnumLabels } from "@/i18n/enumLabels";
import { sevTone, type AnomalySummary } from "@/lib/api";
import {
  detectorLabel,
  evidenceTone,
  formatMetric,
  formatRatio,
  stateTone,
} from "@/lib/behavior";
import { ARROW, evidenceCode } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

function AnomalyTable({
  anomalies,
  label,
  caption,
}: {
  anomalies: AnomalySummary[];
  label: string;
  caption: string;
}) {
  const t = useTranslations("behavior.sourceAnomalies");
  const i18n = useBehaviorI18n();
  const { statePill, severityPill } = useEnumLabels();
  const { locale } = i18n;
  return (
    <TableScroll label={label}>
      <table className="sticky-actions">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{t("detector")}</th>
            <th scope="col">{t("state")}</th>
            <th scope="col" className="num">
              {t("observedExpected")}
            </th>
            <th scope="col" className="num">
              {t("deviation")}
            </th>
            <th scope="col" className="num">
              {t("started")}
            </th>
            <th scope="col">{t("evidence")}</th>
            <th scope="col">
              <span className="sr-only">{t("actions")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {anomalies.map((a) => (
            <tr key={a.id}>
              <td>{detectorLabel(a.anomaly_type, i18n)}</td>
              <td>
                <span className={`pill pill-strong ${stateTone(a.state)}`}>{statePill(a.state)}</span>
              </td>
              <td className="num">
                {formatMetric(a.observed_value, 2, locale)}
                <span className="muted"> {ARROW} </span>
                {formatMetric(a.expected_value, 2, locale)}
              </td>
              <td className="num">{formatRatio(a.deviation_ratio, locale)}</td>
              <td className="num">
                <UtcTime value={a.anomaly_start ?? a.detected_at} variant="compact" />
              </td>
              <td>
                <span className={`pill pill-quiet ${evidenceTone(a.evidence_status)}`}>
                  {evidenceCode(i18n, a.evidence_status)}
                </span>
              </td>
              <td className="row-actions">
                <div className="row-actions-inner">
                  <Link href={`/anomalies/${a.id}`}>{t("open")}</Link>
                  <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                    {severityPill(a.severity)}
                  </span>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function SourceAnomalies({
  active,
  recent,
  activeFailed,
  recentFailed,
  windowLabel,
  unjudged,
}: {
  active: AnomalySummary[];
  recent: AnomalySummary[];
  /** The unwindowed active read failed, so current incident state is unknown. */
  activeFailed: boolean;
  /** The windowed history read failed, so recently closed rows are unknown. */
  recentFailed: boolean;
  windowLabel: string;
  /** The source has no adequate baseline, so nothing has judged it. */
  unjudged: boolean;
}) {
  const t = useTranslations("behavior.sourceAnomalies");
  return (
    <div className="stack-6">
      <div>
        <h3>{t("needsAttention")}</h3>
        {activeFailed ? (
          <p className="notice" role="alert">
            {t("activeFailed")}
          </p>
        ) : active.length === 0 ? (
          <p className="subtitle">
            {t("noActive")}
          </p>
        ) : (
          <AnomalyTable
            anomalies={active}
            label={t("activeLabel")}
            caption={t("activeCaption")}
          />
        )}
      </div>

      <div>
        <h3>{t("recentlyClosed")}</h3>
        {recentFailed ? (
          <p className="notice" role="alert">
            {t("historyFailed")}
          </p>
        ) : recent.length === 0 ? (
          <p className="subtitle">
            {unjudged ? t("noneUnjudged") : t("noneInWindow", { range: windowLabel })}
          </p>
        ) : (
          <AnomalyTable
            anomalies={recent}
            label={t("closedLabel")}
            caption={t("closedCaption")}
          />
        )}
      </div>
    </div>
  );
}
