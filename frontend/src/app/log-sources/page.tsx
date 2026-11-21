import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { plainCount } from "@/lib/fleetText";
import { formatCount, type FormatLocale } from "@/lib/format";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type LogSourceSummary } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { UtcTime } from "@/components/ui/UtcTime";
import { withRouteMessages } from "@/i18n/RouteMessages";


import "./log-sources.css";
import { LOG_SOURCES_PAGE_SIZE, Q_MAX, parseLogSourceQuery, type RawQuery } from "./query";
import { qradarStatusLabel, qradarStatusTone } from "./qradarStatus";

async function LogSourcesPage({ searchParams }: { searchParams: Promise<RawQuery> }) {
  const { q, offset } = parseLogSourceQuery(await searchParams);
  const t = await getTranslations("logSources");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;
  let sources: LogSourceSummary[] = [];
  let total = 0;
  let error: string | null = null;
  try {
    const page = await api.logSources({ q, limit: LOG_SOURCES_PAGE_SIZE, offset });
    sources = page.items;
    total = page.total;
  } catch (err) {
    error = loadErrorMessage(err, t("loadNoun"), "read:log_sources", say);
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/log-sources")}
        actions={<Link href="/log-sources/jdbc">{t("viewJdbc")}</Link>}
        title={t("title")}
        description={
          // A failed read has no count: "0 sources" would claim an empty inventory.
          error ? undefined : t("countLead", { count: formatCount(total, locale) })
        }
      />

      {(q || total > 0 || offset > 0) && !error && (
        <form className="filters" method="get" action="/log-sources" role="search">
          <label className="sr-only" htmlFor="log-source-q">
            {t("searchLabel")}
          </label>
          <input
            id="log-source-q"
            type="search"
            name="q"
            defaultValue={q}
            placeholder={t("searchPlaceholder")}
            maxLength={Q_MAX}
            autoComplete="off"
          />
          <button type="submit">{t("apply")}</button>
          {q && <Link href="/log-sources">{t("clearSearch")}</Link>}
        </form>
      )}

      {error ? (
        <div className="notice" role="alert">
          {error}
        </div>
      ) : sources.length === 0 ? (
        <div className="notice">
          {q ? (
            t("noMatch", { q })
          ) : total > 0 ? (
            <>
              {t("pastEnd")} <Link href="/log-sources">{t("firstPage")}</Link>
            </>
          ) : (
            t("empty")
          )}
        </div>
      ) : (
        <>
        <TableScroll label={t("table.label")}>
          <table className="table-dense log-sources-table" aria-label={t("table.label")}>
            <thead>
              <tr>
                <th>{t("table.name")}</th>
                <th>{t("table.type")}</th>
                <th>{t("table.criticality")}</th>
                <th>{t("table.status")}</th>
                <th className="num">{t("table.anomalies")}</th>
                <th>{t("table.lastEvent")}</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="cell-name">
                    <Link href={`/log-sources/${s.id}`}>{s.name}</Link>
                    {s.maintenance_mode && (
                      <span className="pill warn log-source-maint"> {t("table.maint")}</span>
                    )}
                  </td>
                  <td>{s.type_name ?? "—"}</td>
                  <td>{enumLabel(say, "severityPill", s.criticality)}</td>
                  <td>
                    <span className={`pill ${qradarStatusTone(s.qradar_status)}`}>
                      {qradarStatusLabel(s.qradar_status, say)}
                    </span>
                  </td>
                  <td className="num">
                    {s.open_anomaly_count > 0 ? (
                      <span className="pill warn">{plainCount(s.open_anomaly_count, locale)}</span>
                    ) : (
                      "0"
                    )}
                  </td>
                  <td className="nowrap">
                    {s.last_event_time ? (
                      <UtcTime value={s.last_event_time} variant="compact" />
                    ) : (
                      t("table.never")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
        <Pagination
          total={total}
          limit={LOG_SOURCES_PAGE_SIZE}
          offset={offset}
          basePath="/log-sources"
          params={{ q }}
        />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/log-sources", LogSourcesPage);
