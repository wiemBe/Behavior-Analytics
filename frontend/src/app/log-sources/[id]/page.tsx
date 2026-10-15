import { PageHeader } from "@/components/ui/PageHeader";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/health";

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
  const hb = d.health_breakdown;

  return (
    <>
      {/* An h2: the page test pins this heading at level 2. See the report's open points. */}
      <header className="page-header detail-header">
        <div>
          <h2 className="page-title">{d.name}</h2>
          <p className="page-desc">
            {d.type_name ?? "unknown type"} · criticality {d.criticality} · owner {d.owner ?? "—"}
          </p>
        </div>
      </header>

      {hb ? (
        <div className="grid">
          <div className="card">
            <div className="k">Overall Health</div>
            <div className="v">
              {hb.score}
              <span className="v-unit"> / 100</span>
            </div>
          </div>
          <div className="card">
            <div className="k">Freshness (40%)</div>
            <div className="v">{hb.freshness}</div>
          </div>
          <div className="card">
            <div className="k">Volume (25%)</div>
            <div className="v">{hb.volume}</div>
          </div>
          <div className="card">
            <div className="k">Parsing (20%)</div>
            <div className="v">{hb.parsing}</div>
          </div>
          <div className="card">
            <div className="k">Collection (15%)</div>
            <div className="v">{hb.collection}</div>
          </div>
        </div>
      ) : (
        <div className="notice" role="status">
          No health score yet. It is computed from freshness, volume, parsing and collection
          once this source has been measured.
        </div>
      )}

      <div className="card detail-card">
        <div className="k">Configuration</div>
        <p>Description: {d.description ?? "—"}</p>
        <p>Business hours only: {d.business_hours_only ? "yes" : "no"}</p>
        <p>Expected interval: {d.expected_interval_seconds ?? "—"} s</p>
        <p>Last event: {d.last_event_time ? formatDateTime(d.last_event_time) : "never"}</p>
        <p>QRadar status: {d.qradar_status ?? "unknown"}</p>
      </div>

      <p className="subtitle detail-note">
        EPS trend, anomaly timeline and baseline overlay charts (Apache ECharts) arrive in Phase 2
        once metric collection is populating the time-series tables.
      </p>
    </>
  );
}
