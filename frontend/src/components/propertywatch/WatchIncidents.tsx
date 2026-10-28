import Link from "next/link";
import { useTranslations } from "next-intl";
// The Incidents tab: this watch's rows in `/anomalies`, with the lifecycle each one is in.
// A null list is "could not be loaded", which is not "there are none".

import type { AnomalySummary } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { useEnumLabels } from "@/i18n/enumLabels";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { pwTypeLabel } from "./pwFormat";
import { valueText } from "./WatchValuesTable";

export const INCIDENT_WINDOW_DAYS = 30;

export function WatchIncidents({
  incidents,
  total,
  watchId,
}: {
  incidents: AnomalySummary[] | null;
  total: number | null;
  watchId: string;
}) {
  const t = useTranslations("propertyWatch.incidents");
  const common = useTranslations("propertyWatch.common");
  const i18n = useBehaviorI18n();
  const { statePill, severityPill } = useEnumLabels();
  if (incidents === null) {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("failedTitle")}</strong>
        </p>
        <p>
          {t("failedBody")}
        </p>
      </div>
    );
  }
  if (incidents.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("emptyTitle", { days: INCIDENT_WINDOW_DAYS })}</strong>
        </p>
        <p>
          {t("emptyBody")}
        </p>
      </div>
    );
  }
  return (
    <div className="stack-6">
      <p className="subtitle">
        {total !== null && total > incidents.length
          ? t.rich("introLimited", {
              days: INCIDENT_WINDOW_DAYS,
              shown: incidents.length,
              total,
              link: (chunks) => (
                <Link href={`/anomalies?subject_kind=PW_VALUE&property_watch_id=${watchId}`}>
                  {chunks}
                </Link>
              ),
            })
          : t.rich("introAll", {
              days: INCIDENT_WINDOW_DAYS,
              link: (chunks) => (
                <Link href={`/anomalies?subject_kind=PW_VALUE&property_watch_id=${watchId}`}>
                  {chunks}
                </Link>
              ),
            })}
      </p>
      <TableScroll label={t("tableLabel")}>
        <table className="table-dense" aria-label={t("tableLabel")}>
          <thead>
            <tr>
              <th scope="col">{common("value")}</th>
              <th scope="col">{t("detector")}</th>
              <th scope="col">{common("state")}</th>
              <th scope="col">{common("severity")}</th>
              <th scope="col" className="num">{t("started")}</th>
              <th scope="col" className="num">{t("resolved")}</th>
              <th scope="col">
                <span className="sr-only">{common("actions")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((a) => (
              <tr key={a.id}>
                <td className="cell-name pw-value-cell">
                  <span className="pw-value-text">{a.pw_value ? valueText(a.pw_value, i18n) : "—"}</span>
                </td>
                <td>{pwTypeLabel(a.anomaly_type, i18n)}</td>
                <td>
                  <span className={`pill pill-strong ${stateTone(a.state)}`}>{statePill(a.state)}</span>
                </td>
                <td>
                  <span className={`pill pill-quiet ${sevTone(a.severity)}`}>{severityPill(a.severity)}</span>
                </td>
                <td className="num cell-when">
                  <UtcTime value={a.anomaly_start ?? a.detected_at} />
                </td>
                <td className="num cell-when">
                  {a.resolved_at ? (
                    <UtcTime value={a.resolved_at} />
                  ) : (
                    <span className="muted">{common("notResolved")}</span>
                  )}
                </td>
                <td className="row-actions">
                  <Link href={`/anomalies/${a.id}`}>{t("open")}</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}
