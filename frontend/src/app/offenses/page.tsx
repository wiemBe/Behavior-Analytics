import Link from "next/link";
import { api, type OffenseAggregates, type OffenseSummary, type Page } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { StatCard } from "@/components/StatCard";
import { formatCount } from "@/lib/format";
import { formatAge, magnitudeTone, offenseStatusTone } from "@/lib/health";
import { Pagination } from "@/components/Pagination";
import { OffenseFilters } from "@/components/OffenseFilters";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

import "./offenses.css";

const PAGE_SIZE = 25;

const NOT_COLLECTED = "No offense snapshot has been collected yet.";

const EMPTY_LIST =
  "No offenses have been collected from QRadar yet, so this list is empty. Offense collection is set up by an administrator.";

/** A nullable open count. Null is an em dash; the group states why once. A measured 0 stays 0. */
function offenseStat(label: string, count: number | null, alarm?: "crit" | "warn") {
  if (count == null) return <StatCard label={label} value="—" />;
  return <StatCard label={label} value={formatCount(count)} tone={alarm && count > 0 ? alarm : undefined} />;
}

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
        <>
          <div className="cards offenses-matching">
            <StatCard label="Matching offenses" value={formatCount(page?.total ?? 0)} />
          </div>
          <h2>Open offenses, whole instance</h2>
          {(aggregates.active == null ||
            aggregates.critical == null ||
            aggregates.unassigned == null ||
            aggregates.exceeding_sla == null ||
            aggregates.oldest_age_seconds == null) && (
            <p className="notice" role="status">
              Not collected. {NOT_COLLECTED}
            </p>
          )}
          <div className="cards offenses-open">
            {offenseStat("Active", aggregates.active)}
            {offenseStat(
              `Critical (mag ${aggregates.critical_magnitude}+)`,
              aggregates.critical,
              "crit",
            )}
            {offenseStat("Unassigned", aggregates.unassigned, "warn")}
            {offenseStat(`Over ${aggregates.sla_hours}h SLA`, aggregates.exceeding_sla, "warn")}
            <StatCard label="Oldest" value={formatAge(aggregates.oldest_age_seconds)} />
          </div>
        </>
      )}

      <OffenseFilters search={search} status={status} />

      {items.length === 0 ? (
        <div className="notice">
          {search || status
            ? "No offenses match these filters."
            : EMPTY_LIST}
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
                    <Link href={`/offenses/${o.qradar_offense_id}`}>{o.qradar_offense_id}</Link>
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
                    <span className={`pill ${offenseStatusTone(o.status)}`}>
                      {o.status}
                    </span>
                  </td>
                  <td>{o.assigned_to ?? <span className="muted">unassigned</span>}</td>
                  <td className="num">
                    {o.event_count == null ? "—" : o.event_count.toLocaleString("en-US")}
                  </td>
                  <td>{o.start_time ? <UtcTime value={o.start_time} wrapZone /> : "—"}</td>
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
