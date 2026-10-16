import type { Metadata } from "next";

import { ResultTrend } from "@/components/ResultTrend";
import { ExecutionsTable } from "@/components/searches/ExecutionsTable";
import { ScheduleControl } from "@/components/searches/ScheduleControl";
import { SearchEditor } from "@/components/searches/SearchEditor";
import { SearchEnableControl } from "@/components/searches/SearchEnableControl";
import { SearchRunControl } from "@/components/searches/SearchRunControl";
import { SearchWriteNotice } from "@/components/searches/SearchWriteNotice";
import { previousVersion, queryDiffText, searchAccessFrom, type SearchAccess } from "@/components/searches/authoring";
import { CodePanel } from "@/components/ui/CodePanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type SearchExecution, type SearchVersion } from "@/lib/api";
import { formatDateTime } from "@/lib/health";
import { axisTimeLabel, truncatedRunTimes } from "@/lib/searchRuns";

import "./search-detail.css";

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
  let versions: SearchVersion[] = [];
  let executions: SearchExecution[] = [];
  let access: SearchAccess = "unknown";
  try {
    const [loaded, history, runs, caller] = await Promise.all([
      api.search(id),
      api.searchVersions(id),
      api.searchExecutions(id),
      api.caller().then(searchAccessFrom).catch(() => "unknown" as const),
    ]);
    search = loaded;
    versions = history;
    executions = runs;
    access = caller;
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

  const previous = previousVersion(versions, search.query_version);
  const diff = previous ? queryDiffText(previous.aql_query, search.aql_query) : null;
  const writer = access === "writer";

  return (
    <>
      <PageHeader
        title={search.name}
        meta={
          <span className={search.enabled ? "pill" : "pill warn"}>
            {search.enabled ? "Enabled" : "Disabled"}
          </span>
        }
        description={
          <>
            {search.category ?? "uncategorised"} · severity {search.severity} · v
            {search.query_version}
            {search.next_run_at ? ` · next run ${formatDateTime(search.next_run_at)}` : ""}
          </>
        }
      />

      <div className="search-run-bar">
        <SearchRunControl
          searchId={search.id}
          latest={executions[0] ?? null}
          consecutiveFailures={search.consecutive_failures}
        />
      </div>

      {writer ? (
        <SearchEnableControl id={search.id} enabled={search.enabled} />
      ) : (
        <SearchWriteNotice unknown={access === "unknown"} />
      )}

      {writer ? (
        <section aria-labelledby="definition-title">
          <h2 id="definition-title" className="section-title">
            Definition
          </h2>
          <SearchEditor mode="edit" search={search} />
        </section>
      ) : (
        <section aria-labelledby="schedule-title">
          <h2 id="schedule-title" className="section-title">
            Schedule
          </h2>
          <ScheduleControl searchId={search.id} cron={search.schedule_cron} />
        </section>
      )}

      <section aria-labelledby="aql-title">
        <h2 id="aql-title" className="section-title">
          Current AQL (v{search.query_version})
        </h2>
        <CodePanel label={`Stored query, v${search.query_version}`} code={search.aql_query} meta="read-only" />
        {diff && previous && (
          <CodePanel
            label={`Query change, v${previous.version} to v${search.query_version}`}
            code={diff}
            meta="text"
          />
        )}
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
          <table className="table-dense" aria-label="Query version history">
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
                  <td className="nowrap">{axisTimeLabel(v.created_at)}</td>
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
          <ResultTrend
            searchId={search.id}
            visualization={search.visualization_type}
            search={search}
            executions={executions}
            createdAt={
              versions.find((version) => version.version === 1)?.created_at ??
              versions.reduce<string | null>(
                (earliest, version) =>
                  earliest == null || version.created_at < earliest ? version.created_at : earliest,
                null,
              )
            }
            truncatedAt={truncatedRunTimes(executions)}
          />
        </div>
      </section>
    </>
  );
}
