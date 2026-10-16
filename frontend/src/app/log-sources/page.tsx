import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type LogSourceSummary } from "@/lib/api";
import { formatDateTimeCompact } from "@/lib/health";

import "./log-sources.css";
import { qradarStatusLabel, qradarStatusTone } from "./qradarStatus";

function healthTone(score: number | null): "ok" | "warn" | "crit" | undefined {
  if (score == null) return undefined;
  if (score >= 70) return "ok";
  if (score >= 40) return "warn";
  return "crit";
}

export default async function LogSourcesPage() {
  let sources: LogSourceSummary[] = [];
  let error = false;
  try {
    sources = await api.logSources();
  } catch {
    error = true;
  }

  return (
    <>
      <PageHeader
        title="Log Sources"
        description={
          // A failed read has no count: "0 sources" would claim an empty inventory.
          <>
            {error ? "" : `${sources.length} sources. `}QRadar-owned fields refresh on sync;
            criticality, owner and maintenance are SOC-owned and preserved.
          </>
        }
      />

      {error ? (
        <div className="notice" role="alert">
          Backend unreachable — start the stack, then POST <code>/api/v1/log-sources/sync</code>.
        </div>
      ) : sources.length === 0 ? (
        <div className="notice">
          No log sources yet. Trigger an inventory sync to pull them from the configured provider.
        </div>
      ) : (
        <TableScroll label="Log sources">
          <table className="table-dense log-sources-table" aria-label="Log sources">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Criticality</th>
                <th>Status</th>
                <th className="num">Health</th>
                <th className="num">Anomalies</th>
                <th>Last Event</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="cell-name">
                    <a href={`/log-sources/${s.id}`}>{s.name}</a>
                    {s.maintenance_mode && <span className="pill warn log-source-maint"> maint</span>}
                  </td>
                  <td>{s.type_name ?? "—"}</td>
                  <td>{s.criticality}</td>
                  <td>
                    <span className={`pill ${qradarStatusTone(s.qradar_status)}`}>
                      {qradarStatusLabel(s.qradar_status)}
                    </span>
                  </td>
                  <td className="num">
                    <span className={`pill ${healthTone(s.health_score) ?? ""}`}>
                      {s.health_score ?? "—"}
                    </span>
                  </td>
                  <td className="num">
                    {s.open_anomaly_count > 0 ? (
                      <span className="pill warn">{s.open_anomaly_count}</span>
                    ) : (
                      "0"
                    )}
                  </td>
                  <td className="nowrap">
                    {s.last_event_time ? formatDateTimeCompact(s.last_event_time) : "never"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  );
}
