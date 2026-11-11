import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, sevTone, type AlertFacets, type AlertPage, type LogSourceSummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { loadErrorMessage } from "@/lib/loadError";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { Locale } from "@/i18n/locale";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { alertTitleText } from "@/lib/alertTitle";
import {
  PAGE_SIZE,
  alertFilterParams,
  alertHref,
  alertListParams,
  alertPairs,
  isAlertFiltered,
  isStatusOnly,
  parseAlertQuery,
  sortPatch,
  type AlertSortValue,
  type AlertStatusValue,
  type AlertQuery,
} from "@/lib/alertQuery";
import { rangeWindow } from "@/lib/timeRange";
import { AlertFilters } from "@/components/alerts/AlertFilters";
import { CopyLink } from "@/components/listview/CopyLink";
import { ListKeyboard } from "@/components/listview/ListKeyboard";

import "./alerts.css";

function statusTone(status: string): string {
  if (status === "OPEN") return "crit";
  if (status === "ACKNOWLEDGED") return "warn";
  return "ok";
}

export async function generateMetadata() {
  const t = await getTranslations("alerts.meta");
  return { title: t("list") };
}

/** A sortable column header: a link, so it is keyboard-operable without script. */
function SortHeader({
  label,
  column,
  query,
  title,
}: {
  label: string;
  column: AlertSortValue;
  query: AlertQuery;
  title: string;
}) {
  const active = query.sort === column;
  const aria = !active ? "none" : query.order === "asc" ? "ascending" : "descending";
  const arrow = !active ? "" : query.order === "asc" ? " ↑" : " ↓";
  return (
    <th scope="col" aria-sort={aria}>
      <Link href={alertHref(query, sortPatch(query, column))} className="sort-link" title={title}>
        {label}
        <span aria-hidden="true">{arrow}</span>
      </Link>
    </th>
  );
}

async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const nowMs = Date.now();
  const query = parseAlertQuery(params, nowMs);
  const t = await getTranslations("alerts");
  const l = await getTranslations("alerts.list");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;

  // The list, the counts and the log source names are three reads. Only the list decides
  // whether the page has content: a failed count read or source read costs the counts or the
  // picker and nothing else.
  const [listed, counted, sourced] = await Promise.allSettled([
    api.alerts(alertListParams(query)),
    api.alertFacets(alertFilterParams(query)),
    api.logSources(),
  ]);
  let page: AlertPage | null = null;
  let error: string | null = null;
  if (listed.status === "fulfilled") {
    page = listed.value;
  } else if (listed.reason instanceof ApiError && listed.reason.status === 422) {
    const reason = typeof listed.reason.detail === "string" ? listed.reason.detail.trim() : "";
    error = reason ? l("rejected", { reason }) : l("rejectedNoReason");
  } else {
    error = loadErrorMessage(listed.reason, t("loadNoun"), "read:alerts", say);
  }
  const facets: AlertFacets | null = counted.status === "fulfilled" ? counted.value : null;
  const sourceRows: LogSourceSummary[] = sourced.status === "fulfilled" ? sourced.value : [];
  const sources = sourceRows
    .map((s) => ({ id: s.id, name: s.name }))
    .sort((a, b) => a.name.localeCompare(b.name));
  const alerts = page?.items ?? [];
  const filtered = isAlertFiltered(query);
  const shownWindow = rangeWindow(query.range, nowMs);
  const canonical = alertHref(query, { offset: query.offset });
  const pagination: Record<string, string | string[] | undefined> = {};
  for (const [key, value] of alertPairs(query, false)) {
    const have = pagination[key];
    pagination[key] = have === undefined ? value : [...(Array.isArray(have) ? have : [have]), value];
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/alerts")}
        title={t("title")}
        description={t("description")}
        actions={<CopyLink href={canonical} />}
      />

      {query.ignored.length > 0 && (
        <p className="banner warn alert-note" role="status">
          {query.ignored.map((issue) => t(`ignored.${issue}`)).join(" ")}
        </p>
      )}

      <AlertFilters
        query={query}
        sources={sources}
        sourcesFailed={sourced.status === "rejected"}
        facets={facets}
        window={shownWindow}
      />

      {error ? (
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      ) : alerts.length === 0 ? (
        <div className="notice">
          {!filtered ? (
            t("empty.all")
          ) : isStatusOnly(query) ? (
            t(`empty.${query.status as AlertStatusValue}`)
          ) : (
            <>
              {l("noMatch")} <Link href="/alerts">{l("clearFilters")}</Link>
            </>
          )}
        </div>
      ) : (
        <>
          {page && page.total > alerts.length && (
            <p className="subtitle">
              {t(filtered && !isStatusOnly(query) ? "list.showingFiltered" : "showing", {
                shown: formatCount(alerts.length, locale),
                total: formatCount(page.total, locale),
              })}
            </p>
          )}
          <ListKeyboard table="table.alerts-list" open="td.alert-title a" acknowledge />
          <TableScroll label={t("table.label")}>
            <table className="table-dense alerts-list" aria-label={t("table.label")}>
              <thead>
                <tr>
                  <th scope="col">{t("table.title")}</th>
                  <th scope="col">{t("table.anomaly")}</th>
                  <SortHeader
                    label={t("table.severity")}
                    column="severity"
                    query={query}
                    title={l("sortBy", { column: t("table.severity") })}
                  />
                  <th scope="col">{t("table.status")}</th>
                  <th scope="col" className="num">{t("table.occurrences")}</th>
                  <SortHeader
                    label={t("table.firstSeen")}
                    column="created_at"
                    query={query}
                    title={l("sortBy", { column: t("table.firstSeen") })}
                  />
                  <th scope="col">{t("table.lastSeen")}</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr
                    key={a.id}
                    data-alert-id={a.id}
                    data-alert-status={a.status}
                    data-alert-title={alertTitleText(a, locale, say)}
                  >
                    <td className="alert-title">
                      <Link href={`/alerts/${a.id}`}>
                        {alertTitleText(a, locale, say)}
                      </Link>
                    </td>
                    <td className="alert-anomaly">
                      {a.source_anomaly_ids.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <Link
                          href={`/anomalies/${a.source_anomaly_ids[0]}`}
                          aria-label={t("anomalyLinkLabel", { title: alertTitleText(a, locale, say) })}
                        >
                          {a.source_anomaly_ids.length > 1
                            ? t("anomalyLinkMore", { count: a.source_anomaly_ids.length - 1 })
                            : t("anomalyLink")}
                        </Link>
                      )}
                    </td>
                    <td><span className={`pill ${sevTone(a.severity)}`}>{enumLabel(say, "severityPill", a.severity)}</span></td>
                    <td><span className={`pill ${statusTone(a.status)}`}>{enumLabel(say, "alertStatusPill", a.status)}</span></td>
                    <td className="num">{formatCount(a.occurrence_count, locale)}</td>
                    <td className="nowrap">{<UtcTime value={a.first_seen_at} variant="compact" />}</td>
                    <td className="nowrap">{<UtcTime value={a.last_seen_at} variant="compact" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={query.offset}
            basePath="/alerts"
            params={pagination}
          />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/alerts", AlertsPage);
