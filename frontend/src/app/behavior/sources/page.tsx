// The source-behavior inventory.
//
// This route owns the full monitored-source list, which previously sat at the
// bottom of the behavioral overview. Moving it here is the point: on a fleet
// of 59 sources of which 55 are quiet and normal, that table filled the
// overview with rows requiring no action and pushed the ones requiring action
// off the screen. The overview is now a worklist; this is the inventory, and
// an analyst arrives here deliberately.
//
// Columns are kept to what identifies and ranks a source. Baseline versions,
// provenance and collection internals belong on the source's own page, where
// there is room to explain them.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { SourceFilters } from "@/components/behavior/SourceFilters";
import { SourceRowQualifier } from "@/components/behavior/SourceRowQualifier";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, type SourceBehavior } from "@/lib/api";
import { formatMetric, formatRatio, isUnjudged, stateTone } from "@/lib/behavior";
import { getBehaviorI18n, getEnumLabels } from "@/lib/behaviorI18n.server";
import { plainCount } from "@/lib/figureText";
import {
  SOURCE_PAGE_SIZE,
  ariaSort,
  filterSources,
  isFiltered,
  parseSourceQuery,
  sliceSources,
  sortHref,
  sortSources,
  sourceQueryParams,
} from "@/lib/sourceInventory";
import { UtcTime } from "@/components/ui/UtcTime";


import "./sources.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("behavior.sources");
  return { title: t("metaTitle") };
}

/** A sortable column header: a link, so it is keyboard-operable for free. */
function SortHeader({
  label,
  column,
  query,
  numeric = false,
}: {
  label: string;
  column: "deviation" | "last_event" | "name";
  query: ReturnType<typeof parseSourceQuery>;
  /** A numeric column is right-aligned, header included. */
  numeric?: boolean;
}) {
  const sort = ariaSort(query, column);
  const arrow = sort === "none" ? "" : sort === "ascending" ? " ↑" : " ↓";
  return (
    <th scope="col" aria-sort={sort} className={numeric ? "num" : undefined}>
      <Link href={sortHref(query, column)} className="sort-link">
        {label}
        <span aria-hidden="true">{arrow}</span>
      </Link>
    </th>
  );
}

export default async function SourcesIndexPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = parseSourceQuery(await searchParams);
  const t = await getTranslations("behavior.sources");
  const errors = await getTranslations("behavior.errors");
  const { locale } = await getBehaviorI18n();
  const { statePill } = await getEnumLabels();

  let sources: SourceBehavior[] = [];
  let error: string | null = null;
  try {
    sources = await api.sourceBehaviors();
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? errors("forbidden")
        : err instanceof ApiError && err.status === 401
          ? errors("expired")
          : t("failed");
  }

  const matched = sortSources(
    filterSources(sources, query),
    query.sort,
    query.dir,
  );
  const visible = sliceSources(matched, query.offset);

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
      />

      <SourceFilters query={query} />

      {error ? (
        <div className="notice" role="alert">
          {error}
        </div>
      ) : matched.length === 0 ? (
        <div className="notice">
          {isFiltered(query) ? t("noMatch") : t("none")}
        </div>
      ) : (
        <>
          <TableScroll label={t("label")}>
            <table className="source-inventory">
              <caption className="sr-only">
                {t("caption")}
              </caption>
              <thead>
                <tr>
                  <SortHeader label={t("source")} column="name" query={query} />
                  <th scope="col">{t("state")}</th>
                  <th scope="col" className="num">
                    {t("observed")}
                  </th>
                  <th scope="col" className="num">
                    {t("expected")}
                  </th>
                  <SortHeader label={t("deviation")} column="deviation" query={query} numeric />
                  <SortHeader label={t("lastEvent")} column="last_event" query={query} numeric />
                  <th scope="col" className="num">
                    {t("anomalies")}
                  </th>
                  <th scope="col" className="col-detail">
                    <span className="sr-only">{t("detail")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((s) => (
                  <tr key={s.log_source_id}>
                    <td className="cell-name">
                      <Link href={`/behavior/sources/${s.log_source_id}`}>{s.name}</Link>
                      <SourceRowQualifier source={s} />
                    </td>
                    <td>
                      <span className={`pill ${stateTone(s.state)}`}>{statePill(s.state)}</span>
                    </td>
                    <td className="num">{formatMetric(s.observed_eps, 2, locale)}</td>
                    {/* An unbaselined source has no expectation at all.
                        Rendering a 0 here would invent one, and make the
                        observed value look like a spike against it. */}
                    <td className="num">
                      {isUnjudged(s.state) ? (
                        <span className="muted">{t("stillLearning")}</span>
                      ) : (
                        formatMetric(s.expected_eps, 2, locale)
                      )}
                    </td>
                    <td className="num">{formatRatio(s.deviation_ratio, locale)}</td>
                    <td className="num">{<UtcTime value={s.last_event_at} variant="compact" />}</td>
                    <td className="num">
                      {s.open_anomaly_count > 0 ? (
                        <Link
                          href={`/anomalies?log_source_id=${s.log_source_id}&active_only=true`}
                        >
                          {plainCount(s.open_anomaly_count, locale)}
                        </Link>
                      ) : (
                        <span className="muted">{plainCount(0, locale)}</span>
                      )}
                    </td>
                    <td className="col-detail">
                      <Link href={`/behavior/sources/${s.log_source_id}`}>{t("detail")}</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>

          <Pagination
            total={matched.length}
            limit={SOURCE_PAGE_SIZE}
            offset={query.offset}
            basePath="/behavior/sources"
            params={sourceQueryParams(query)}
          />
        </>
      )}
    </>
  );
}
