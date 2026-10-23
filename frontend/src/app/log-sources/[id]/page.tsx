import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type LogSourceDetail } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import { qradarStatusLabel, qradarStatusTone } from "../qradarStatus";

import "./log-source-detail.css";

export default async function LogSourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let d: LogSourceDetail | null = null;
  let status = 0;
  try {
    d = await api.logSource(id);
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
  }
  if (!d) {
    const message =
      status === 401
        ? "Your session has expired. Sign in again to continue."
        : status === 403
          ? "You do not have permission to view this log source. Ask an administrator for the read:log_sources grant."
          : status === 404
            ? `Log source ${id} is not in the inventory.`
            : "The backend could not be reached. Reload the page to try again.";
    return (
      <>
        <PageHeader title="Log Source Detail" />
        <div className="notice" role="alert">
          <p>{message}</p>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={d.name}
        description={
          <>
            {d.type_name ?? "unknown type"} · criticality {d.criticality} · owner {d.owner ?? "—"}
          </>
        }
      />

      <div className="card detail-card">
        <div className="k">Configuration</div>
        <p>Description: {d.description ?? "—"}</p>
        <p>Business hours only: {d.business_hours_only ? "yes" : "no"}</p>
        <p>
          Expected interval:{" "}
          {d.expected_interval_seconds == null ? "—" : `${d.expected_interval_seconds} s`}
        </p>
        <p>Last event: {d.last_event_time ? <UtcTime value={d.last_event_time} /> : "never"}</p>
        <p>
          QRadar status:{" "}
          <span className={`pill ${qradarStatusTone(d.qradar_status)}`}>
            {qradarStatusLabel(d.qradar_status)}
          </span>
        </p>
      </div>

      <p className="subtitle detail-note">
        EPS trend, anomaly timeline and baseline overlay:{" "}
        <Link href={`/behavior/sources/${d.id}`}>source behavior</Link>.
      </p>
    </>
  );
}
