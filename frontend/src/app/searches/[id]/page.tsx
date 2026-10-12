import type { Metadata } from "next";

import { ResultTrend } from "@/components/ResultTrend";
import { RunSearchButton } from "@/components/RunSearchButton";
import { ExecutionsTable } from "@/components/searches/ExecutionsTable";
import { ScheduleControl } from "@/components/searches/ScheduleControl";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type SearchExecution } from "@/lib/api";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";
import { truncatedRunTimes } from "@/lib/searchRuns";

export const metadata: Metadata = {
  title: "Search",
};

export default async function SearchDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let search;
  let versions;
  let executions: SearchExecution[] = [];
  try {
    [search, versions, executions] = await Promise.all([
      api.search(id),
      api.searchVersions(id),
      api.searchExecutions(id),
    ]);
  } catch {
    return (
      <>
        <PageHeader title="Search detail" />
        <div className="notice" role="alert">
          <p>
            <strong>Search {id} could not be loaded.</strong>
          </p>
          <p>It may have been removed, or the backend may be unreachable. Reload the page to try again.</p>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={search.name}
        description={
          <>
            {search.category ?? "uncategorised"} · severity {search.severity} · v
            {search.query_version}
            {search.next_run_at ? ` · next run ${formatDateTime(search.next_run_at)}` : ""}
          </>
        }
        actions={<RunSearchButton searchId={search.id} />}
      />

      <>
        <section aria-labelledby="schedule-title">
          <h2 id="schedule-title" className="section-title">
            Schedule
          </h2>
          <ScheduleControl searchId={search.id} cron={search.schedule_cron} />
        </section>

        <section aria-labelledby="aql-title">
          <h2 id="aql-title" className="section-title">
            Current AQL (v{search.query_version})
          </h2>
          <div className="card">
            <pre>{search.aql_query}</pre>
          </div>
        </section>

        <section aria-labelledby="exec-title">
          <h2 id="exec-title" className="section-title">
            Execution history
          </h2>
          {executions.length === 0 ? (
            <div className="notice">
              No executions yet. The first appears after this search runs on its schedule or is run
              now.
            </div>
          ) : (
            <ExecutionsTable executions={executions} />
          )}
        </section>

        <section aria-labelledby="versions-title">
          <h2 id="versions-title" className="section-title">
            Query version history
          </h2>
          <TableScroll label="Query version history">
            <table>
              <thead>
                <tr>
                  <th scope="col">Version</th>
                  <th scope="col">Changed by</th>
                  <th scope="col">Note</th>
                  <th scope="col">When (UTC)</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version}>
                    <td>v{v.version}</td>
                    <td>{v.changed_by ?? "—"}</td>
                    <td>{v.change_note ?? "—"}</td>
                    <td>{formatDateTimeCompact(v.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
        </section>

        <section aria-labelledby="trend-title">
          <h2 id="trend-title" className="section-title">
            Result trend
          </h2>
          <div className="card">
            <ResultTrend searchId={search.id} truncatedAt={truncatedRunTimes(executions)} />
          </div>
        </section>
      </>
    </>
  );
}
