import { AlertActions } from "@/components/AlertActions";
import { Evidence } from "@/components/Evidence";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type Notification } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { axisTimeLabel } from "@/lib/searchRuns";
import { UtcTime } from "@/components/ui/UtcTime";


import "./alert-detail.css";

function notifTone(status: string): string {
  if (status === "SENT") return "ok";
  if (status === "DEAD_LETTER") return "crit";
  if (status === "FAILED") return "warn";
  return "";
}

export default async function AlertDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let alert;
  let notifications: Notification[] = [];
  try {
    [alert, notifications] = await Promise.all([api.alert(id), api.alertNotifications(id)]);
  } catch {
    return (
      <>
        <PageHeader title="Alert Detail" />
        <div className="notice" role="alert">
          <p>
            <strong>Could not load alert {id}.</strong>
          </p>
          <p>
            It may not exist, or the backend may be unreachable. Check the Alerts list, or reload
            the page to try again.
          </p>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={alert.title}
        description={
          <>
            <span className={`pill ${sevTone(alert.severity)}`}>{alert.severity}</span>{" "}
            <span className="pill">{alert.status}</span> · seen {formatCount(alert.occurrence_count)}× ·
            fingerprint <code>{alert.fingerprint}</code>
          </>
        }
      />

      <div className="card">
        <div className="k">Evidence</div>
        <div className="alert-evidence">
          <Evidence data={alert.evidence_snapshot} />
        </div>
      </div>

      <div className="card alert-lifecycle">
        <div className="k">Lifecycle</div>
        <p>Opened: {<UtcTime value={alert.opened_at} />}</p>
        <p>Acknowledged by: {alert.acknowledged_by ?? "—"}</p>
        <p>Resolved by: {alert.resolved_by ?? "—"}</p>
        <p>Resolution: {alert.resolution_reason ?? "—"}</p>
        <p>Source anomalies: {alert.source_anomaly_ids.length}</p>
      </div>

      <AlertActions alertId={alert.id} status={alert.status} />

      <h3>Notification Delivery History</h3>
      {notifications.length === 0 ? (
        <div className="notice">No notifications for this alert.</div>
      ) : (
        <TableScroll label="Notification delivery history">
          <table className="table-dense delivery-table" aria-label="Notification delivery history">
            <thead>
              <tr>
                <th>Channel</th>
                <th>Transition</th>
                <th>Status</th>
                <th className="num">Attempts</th>
                <th>Sent (UTC)</th>
                <th>Next attempt (UTC)</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {notifications.map((n) => (
                <tr key={n.id}>
                  <td>{n.channel}</td>
                  <td>{n.transition}</td>
                  <td><span className={`pill ${notifTone(n.status)}`}>{n.status}</span></td>
                  <td className="num">{n.attempts}/{n.max_attempts}</td>
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
