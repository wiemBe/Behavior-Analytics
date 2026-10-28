import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ExecutionsTable } from "@/components/searches/ExecutionsTable";
import { ScheduleControl } from "@/components/searches/ScheduleControl";
import { SearchEditor } from "@/components/searches/SearchEditor";
import { SearchEnableControl } from "@/components/searches/SearchEnableControl";
import { SearchResultTrend } from "@/components/searches/SearchResultTrend";
import { SearchRunControl } from "@/components/searches/SearchRunControl";
import { SearchWriteNotice } from "@/components/searches/SearchWriteNotice";
import { previousVersion, queryDiffText, searchAccessFrom, type SearchAccess } from "@/components/searches/authoring";
import { versionTag } from "@/components/searches/searchesText";
import { CodePanel } from "@/components/ui/CodePanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { api, type SearchExecution, type SearchVersion } from "@/lib/api";
import { axisTimeLabel, truncatedRunTimes } from "@/lib/searchRuns";
import { UtcTime } from "@/components/ui/UtcTime";


import "./search-detail.css";

// The trend is ResultTrend, mounted by SearchResultTrend: a server page cannot hand a translator
// function to a client component, so the client wrapper reads the interface language and passes it.

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("searches.meta");
  return { title: t("detail") };
}

export default async function SearchDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("searches.detail");
  const say = asTextTranslator(await getTranslations());
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
        <PageHeader title={t("title")} />
        <div className="notice" role="alert">
          <p>
            <strong>{t("loadFailed", { id })}</strong>
          </p>
          <p>{t("loadFailedHelp")}</p>
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
            {search.enabled ? t("enabled") : t("disabled")}
          </span>
        }
        description={
          <>
            {t("meta", {
              category: search.category ?? t("uncategorised"),
              severity: enumLabel(say, "severityPill", search.severity),
              version: search.query_version,
            })}
            {search.next_run_at
              ? t.rich("nextRun", { time: () => <UtcTime value={search.next_run_at} /> })
              : ""}
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
            {t("definition")}
          </h2>
          <SearchEditor mode="edit" search={search} />
        </section>
      ) : (
        <section aria-labelledby="schedule-title">
          <h2 id="schedule-title" className="section-title">
            {t("schedule")}
          </h2>
          <ScheduleControl searchId={search.id} cron={search.schedule_cron} />
        </section>
      )}

      <section aria-labelledby="aql-title">
        <h2 id="aql-title" className="section-title">
          {t("currentAql", { version: search.query_version })}
        </h2>
        <CodePanel
          label={t("storedQuery", { version: search.query_version })}
          code={search.aql_query}
          meta={t("readOnly")}
        />
        {diff && previous && (
          <CodePanel
            label={t("queryChange", { from: previous.version, to: search.query_version })}
            code={diff}
            meta={t("text")}
          />
        )}
      </section>

      <section aria-labelledby="exec-title">
        <h2 id="exec-title" className="section-title">
          {t("executionHistory")}
        </h2>
        {executions.length === 0 ? (
          <div className="notice">{t("noExecutions")}</div>
        ) : (
          <ExecutionsTable executions={executions} />
        )}
      </section>

      <section aria-labelledby="versions-title">
        <h2 id="versions-title" className="section-title">
          {t("versionHistory")}
        </h2>
        <TableScroll label={t("versionHistory")}>
          <table className="table-dense" aria-label={t("versionHistory")}>
            <thead>
              <tr>
                <th scope="col">{t("version")}</th>
                <th scope="col">{t("changedBy")}</th>
                <th scope="col">{t("note")}</th>
                <th scope="col">{t("when")}</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version}>
                  <td>{versionTag(v.version)}</td>
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
          {t("resultTrend")}
        </h2>
        <div className="card">
          <SearchResultTrend
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
