import Link from "next/link";
import { PageHeader } from "@/components/ui/PageHeader";
import { api } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import { qradarStatusLabel, qradarStatusTone } from "../qradarStatus";

import "./log-source-detail.css";

export default async function LogSourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const d = await api.logSource(id).catch(() => null);
  if (!d) {
    return (
      <>
        <PageHeader title="Log Source Detail" />
        <div className="notice" role="alert">
          <p>
            <strong>Could not load log source {id}.</strong>
          </p>
          <p>
            It may not exist in the inventory, or the backend may be unreachable. Check the
            Log Sources list, or reload the page to try again.
          </p>
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
