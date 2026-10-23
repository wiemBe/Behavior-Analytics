import Link from "next/link";
// The Incidents tab: this watch's rows in `/anomalies`, with the lifecycle each one is in.
// A null list is "could not be loaded", which is not "there are none".

import type { AnomalySummary } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";

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
  if (incidents === null) {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>This watch&apos;s incidents could not be loaded.</strong>
        </p>
        <p>
          The rest of the page is unaffected, but an empty list here would mean nothing: reload the
          page to try again. Nothing was changed.
        </p>
      </div>
    );
  }
  if (incidents.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>No incident was detected on this watch in the last {INCIDENT_WINDOW_DAYS} days.</strong>
        </p>
        <p>
          Values that are still learning, and buckets that were lower bounds or not collected, are
          not judged, so an empty list is not a statement that every value is normal.
        </p>
      </div>
    );
  }
  return (
    <div className="stack-6">
      <p className="subtitle">
        Incidents detected in the last {INCIDENT_WINDOW_DAYS} days, newest first, in UTC.
        {total !== null && total > incidents.length
          ? ` ${incidents.length} of ${total} are listed here; the rest are in `
          : " They are also in "}
        <Link href={`/anomalies?subject_kind=PW_VALUE&property_watch_id=${watchId}`}>Anomalies</Link>.
      </p>
      <TableScroll label="Incidents of this watch">
        <table className="table-dense" aria-label="Incidents of this watch">
          <thead>
            <tr>
              <th scope="col">Value</th>
              <th scope="col">Detector</th>
              <th scope="col">State</th>
              <th scope="col">Severity</th>
              <th scope="col" className="num">Started (UTC)</th>
              <th scope="col" className="num">Resolved (UTC)</th>
              <th scope="col">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {incidents.map((a) => (
              <tr key={a.id}>
                <td className="cell-name pw-value-cell">
                  <span className="pw-value-text">{a.pw_value ? valueText(a.pw_value) : "—"}</span>
                </td>
                <td>{pwTypeLabel(a.anomaly_type)}</td>
                <td>
                  <span className={`pill pill-strong ${stateTone(a.state)}`}>{a.state}</span>
                </td>
                <td>
                  <span className={`pill pill-quiet ${sevTone(a.severity)}`}>{a.severity}</span>
                </td>
                <td className="num cell-when">
                  <UtcTime value={a.anomaly_start ?? a.detected_at} />
                </td>
                <td className="num cell-when">
                  {a.resolved_at ? (
                    <UtcTime value={a.resolved_at} />
                  ) : (
                    <span className="muted">not resolved</span>
                  )}
                </td>
                <td className="row-actions">
                  <Link href={`/anomalies/${a.id}`}>Open</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}
