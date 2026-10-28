import { AlertActions } from "@/components/AlertActions";
import { getLocale, getTranslations } from "next-intl/server";
import { Evidence } from "@/components/Evidence";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type Notification } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { axisTimeLabel } from "@/lib/searchRuns";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { Locale } from "@/i18n/locale";


import "./alert-detail.css";

function notifTone(status: string): string {
  if (status === "SENT") return "ok";
  if (status === "DEAD_LETTER") return "crit";
  if (status === "FAILED") return "warn";
  return "";
}

export async function generateMetadata() {
  const t = await getTranslations("alerts.meta");
  return { title: t("detail") };
}

export default async function AlertDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("alerts.detail");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;
  let alert;
  let notifications: Notification[] = [];
  try {
    [alert, notifications] = await Promise.all([api.alert(id), api.alertNotifications(id)]);
  } catch {
    return (
      <>
        <PageHeader guide={guideFor("/alerts/[id]")} title={t("title")} />
        <div className="notice" role="alert">
          <p>
            <strong>{t("failedTitle", { id })}</strong>
          </p>
          <p>
            {t("failedHelp")}
          </p>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/alerts/[id]")}
        title={alert.title}
        description={
          <>
            <span className={`pill ${sevTone(alert.severity)}`}>
              {enumLabel(say, "severityPill", alert.severity)}
            </span>{" "}
            <span className="pill">{enumLabel(say, "alertStatusPill", alert.status)}</span> ·{" "}
            {t("seen", { count: formatCount(alert.occurrence_count, locale) })} ·{" "}
            {t("fingerprint")} <code>{alert.fingerprint}</code>
          </>
        }
      />

      <div className="card">
        <div className="k">{t("evidence")}</div>
        <div className="alert-evidence">
          <Evidence data={alert.evidence_snapshot} />
        </div>
      </div>

      <div className="card alert-lifecycle">
        <div className="k">{t("lifecycle")}</div>
        <p>{t("openedLabel")} {<UtcTime value={alert.opened_at} />}</p>
        <p>{t("acknowledgedByLabel")} {alert.acknowledged_by ?? "—"}</p>
        <p>{t("resolvedByLabel")} {alert.resolved_by ?? "—"}</p>
        <p>{t("resolutionLabel")} {alert.resolution_reason ?? "—"}</p>
        <p>{t("sourceAnomalies", { count: formatCount(alert.source_anomaly_ids.length, locale) })}</p>
      </div>

      <AlertActions alertId={alert.id} status={alert.status} />

      <h3>{t("notifications")}</h3>
      {notifications.length === 0 ? (
        <div className="notice">{t("notificationsEmpty")}</div>
      ) : (
        <TableScroll label={t("notificationsLabel")}>
          <table className="table-dense delivery-table" aria-label={t("notificationsLabel")}>
            <thead>
              <tr>
                <th>{t("channel")}</th>
                <th>{t("transition")}</th>
                <th>{t("status")}</th>
                <th className="num">{t("attempts")}</th>
                <th>{t("sent")}</th>
                <th>{t("nextAttempt")}</th>
                <th>{t("error")}</th>
              </tr>
            </thead>
            <tbody>
              {notifications.map((n) => (
                <tr key={n.id}>
                  <td>{enumLabel(say, "notificationChannel", n.channel)}</td>
                  <td>{enumLabel(say, "alertTransition", n.transition)}</td>
                  <td><span className={`pill ${notifTone(n.status)}`}>{enumLabel(say, "notificationStatus", n.status)}</span></td>
                  <td className="num">{t("attemptCount", {
                    attempts: formatCount(n.attempts, locale),
                    maximum: formatCount(n.max_attempts, locale),
                  })}</td>
                  <td className="nowrap">{axisTimeLabel(n.sent_at)}</td>
                  <td className="nowrap">{axisTimeLabel(n.next_attempt_at)}</td>
                  <td className="cell-error">{n.error_message ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  );
}
