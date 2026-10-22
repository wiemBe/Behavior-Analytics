import { api, type OffenseAggregates, type OffenseSummary, type Page } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { StatCard } from "@/components/StatCard";
import { formatAge, magnitudeTone } from "@/lib/health";
import { axisTimeLabel } from "@/lib/searchRuns";
import { Pagination } from "@/components/Pagination";
import { OffenseFilters } from "@/components/OffenseFilters";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";

import "@/components/ui/controls.css";

import "./offenses.css";

const PAGE_SIZE = 25;

/** Magnitude is 0 to 10; the bar is drawn in whole tenths of the track. */
function magnitudeStep(magnitude: number): number {
  return Math.min(10, Math.max(0, Math.round(magnitude)));
}

export default async function OffensesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const search = params.search ?? "";
  const status = params.status ?? "";

  let page: Page<OffenseSummary> | null = null;
  let aggregates: OffenseAggregates | null = null;
  let error: string | null = null;

  try {
    // Fetched together: the counters describe the whole instance, not the
    // current page, so they must not be derived from `items`.
    [page, aggregates] = await Promise.all([
      api.offenses({ limit: PAGE_SIZE, offset, search, status }),
      api.offenseAggregates(),
    ]);
  } catch (err) {
    error = loadErrorMessage(err, "offenses", "read:offenses");
  }

  if (error) {
    return (
      <>
        <PageHeader title="Offenses" />
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      </>
    );
  }

  const items = page?.items ?? [];

  return (
    <>
      {/* Neither read says how far the offense snapshots reach: the page has no data time. */}
      <ReportDataAge iso={null} />
      <PageHeader
        title="Offenses"
        description="Read-only view of offenses collected from QRadar. This page issues no writes: offenses are triaged in QRadar, not here."
      />

      {aggregates && (
        <div className="cards offenses-cards offenses-cards-3">
          <StatCard label="Total" value={page?.total ?? 0} />
          <StatCard label="Active" value={aggregates.active} />
          <StatCard
            label={`Critical (mag ${aggregates.critical_magnitude}+)`}
            value={aggregates.critical}
            tone={aggregates.critical > 0 ? "crit" : undefined}
          />
          <StatCard
            label="Unassigned"
            value={aggregates.unassigned}
            tone={aggregates.unassigned > 0 ? "warn" : undefined}
          />
          <StatCard
            label={`Over ${aggregates.sla_hours}h SLA`}
            value={aggregates.exceeding_sla}
            tone={aggregates.exceeding_sla > 0 ? "warn" : undefined}
          />
          <StatCard label="Oldest" value={formatAge(aggregates.oldest_age_seconds)} />
        </div>
      )}

      <OffenseFilters search={search} status={status} />

      {items.length === 0 ? (
        <div className="notice">
          {search || status
            ? "No offenses match these filters."
            : "No offenses collected yet. Run an offense sync to pull them from QRadar."}
        </div>
      ) : (
        <>
          <TableScroll label="Offenses">
          <table className="offenses-table table-dense" aria-label="Offenses">
            <thead>
              <tr>
                <th>ID</th>
                <th>Description</th>
                <th title="Magnitude, 0 to 10">Mag</th>
                <th className="num" title="Severity, 0 to 10">
                  Sev
                </th>
                <th>Status</th>
                <th>Assigned</th>
                <th className="num">Events</th>
                <th>Started (UTC)</th>
                <th className="num">Age</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => (
                <tr key={o.qradar_offense_id}>
                  <td>
                    <a href={`/offenses/${o.qradar_offense_id}`}>{o.qradar_offense_id}</a>
                  </td>
                  {/* Rendered as text, never as markup: this string comes from
                      QRadar. The backend sanitizes it; React escapes it again. */}
                  <td className="cell-description">{o.description?.trim() || "—"}</td>
                  <td className="cell-magnitude">
                    <span className={`pill ${magnitudeTone(o.magnitude)}`}>
                      {o.magnitude ?? "—"}
                    </span>
                    {o.magnitude != null && (
                      <span
                        className={`mag-bar ${magnitudeTone(o.magnitude)} mag-w-${magnitudeStep(o.magnitude)}`}
                        aria-hidden="true"
                      />
                    )}
                  </td>
                  <td className="num">{o.severity ?? "—"}</td>
                  <td>
                    <span className={`pill ${o.status === "OPEN" ? "warn" : "ok"}`}>
                      {o.status}
                    </span>
                  </td>
                  <td>{o.assigned_to ?? <span className="muted">unassigned</span>}</td>
                  <td className="num">
                    {o.event_count == null ? "—" : o.event_count.toLocaleString("en-US")}
                  </td>
                  <td className="nowrap">{o.start_time ? axisTimeLabel(o.start_time) : "—"}</td>
                  <td className="num">{formatAge(o.age_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableScroll>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/offenses"
            params={{ search, status }}
          />
        </>
      )}
    </>
  );
}
