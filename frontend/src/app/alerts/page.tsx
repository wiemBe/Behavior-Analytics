import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type Alert } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { UtcTime } from "@/components/ui/UtcTime";

import "./alerts.css";

function statusTone(status: string): string {
  if (status === "OPEN") return "crit";
  if (status === "ACKNOWLEDGED") return "warn";
  return "ok";
}

export default async function AlertsPage() {
  let alerts: Alert[] = [];
  let error: string | null = null;
  try {
    alerts = await api.alerts();
  } catch (err) {
    error = loadErrorMessage(err, "alerts", "read:alerts");
  }

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Lifecycle OPEN → ACKNOWLEDGED → RESOLVED. Repeated detections update the open alert and bump its occurrence count rather than creating duplicates. Times are UTC."
      />

      {error ? (
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      ) : alerts.length === 0 ? (
        <div className="notice">No alerts.</div>
      ) : (
        <TableScroll label="Alerts">
          <table className="table-dense" aria-label="Alerts">
            <thead>
              <tr>
                <th>Title</th>
                <th>Severity</th>
                <th>Status</th>
                <th className="num">Occurrences</th>
                <th>First seen</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id}>
                  <td className="alert-title">
                    <Link href={`/alerts/${a.id}`}>{a.title}</Link>
                  </td>
                  <td><span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span></td>
                  <td><span className={`pill ${statusTone(a.status)}`}>{a.status}</span></td>
                  <td className="num">{a.occurrence_count}</td>
                  <td className="nowrap">{<UtcTime value={a.first_seen_at} variant="compact" />}</td>
                  <td className="nowrap">{<UtcTime value={a.last_seen_at} variant="compact" />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  );
}
