// The ranked table: one row per WAF endpoint or IPS destination IP, with the average rate drawn
// as one horizontal bar. The bar is `rate / max_rate_per_second`, where the maximum is the whole
// set's, so bars compare across pages. A rate that was not measured has no bar and no zero.

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { Fragment } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import type { IpsTrafficItem, TrafficItemBase, TrafficPage, WafTrafficItem } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import {
  ariaSort,
  formatRate,
  sortHref,
  type TrafficQuery,
  type TrafficSort,
} from "@/lib/traffic";
import { SPARK_H, SPARK_W, sparkOf } from "@/lib/trafficSeries";

import "@/components/ui/controls.css";
import "./traffic.css";

function Trend({
  series,
  data,
}: {
  series: (number | null)[] | undefined;
  data: TrafficPage<TrafficItemBase>;
}) {
  const t = useTranslations("traffic.table");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const points = series ?? [];
  // One point is a value, not a trend: the figure columns already carry it.
  if (data.series_points <= 1 || points.length <= 1 || data.series_step_seconds === null) return <span className="muted">—</span>;
  const spark = sparkOf(points);
  if (spark === null) return <span className="muted">{t("trendNone")}</span>;
  const end =
    data.series_start === null || data.series_step_seconds === null
      ? null
      : new Date(Date.parse(data.series_start) + data.series_points * data.series_step_seconds * 1000);
  const label = t("trendLabel", {
    start: utcTimeText(data.series_start, "compact"),
    end: end ? utcTimeText(end.toISOString(), "compact") : "—",
    low: formatRate(spark.min, locale),
    high: formatRate(spark.max, locale),
    gaps: spark.gaps,
  });
  return (
    <svg
      className="traffic-spark"
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      role="img"
      aria-label={label}
      focusable="false"
    >
      {spark.lines.map((line, i) => (
        <polyline key={i} className="traffic-spark-line" points={line} />
      ))}
      {spark.dots.map((d, i) => (
        <circle key={i} className="traffic-spark-dot" cx={d.x} cy={d.y} r={1.5} />
      ))}
      {spark.peak && (
        <circle className="traffic-spark-peak" cx={spark.peak.x} cy={spark.peak.y} r={2} />
      )}
    </svg>
  );
}

/** A path with a break opportunity after each "/", so it wraps between segments. */
function PathLabel({ text }: { text: string }) {
  const parts = text.split("/");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && (
            <>
              /<wbr />
            </>
          )}
          {part}
        </Fragment>
      ))}
    </>
  );
}

/** A host name with a break opportunity after each dot, so a long one wraps between labels. */
function HostLabel({ text }: { text: string }) {
  const parts = text.split(".");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && (
            <>
              .<wbr />
            </>
          )}
          {part}
        </Fragment>
      ))}
    </>
  );
}

function SortHeader({
  query,
  column,
  label,
  numeric = false,
}: {
  query: TrafficQuery;
  column: TrafficSort;
  label: string;
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

function Flags({ item, measured }: { item: TrafficItemBase; measured: number }) {
  const t = useTranslations("traffic.table");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  return (
    <>
      {!item.active && (
        <span className="cell-sub">
          <span className="pill pill-quiet" title={t("retiredHint")}>
            {t("retired")}
          </span>
        </span>
      )}
      {item.buckets_with_data < measured && (
        <span className="cell-sub" title={t("coverageHint")}>
          {t("coverage", {
            with: formatCount(item.buckets_with_data, locale),
            measured: formatCount(measured, locale),
          })}
        </span>
      )}
      {item.partial_buckets > 0 && (
        <span className="cell-sub">
          <span className="pill pill-quiet warn">
            {t("partial", { count: item.partial_buckets })}
          </span>
        </span>
      )}
    </>
  );
}

function MetricCells({
  item,
  data,
  locale,
}: {
  item: TrafficItemBase;
  data: TrafficPage<TrafficItemBase>;
  locale: FormatLocale;
}) {
  const t = useTranslations("traffic.table");
  return (
    <>
      <td className="num">
        {item.rate_per_second === null ? (
          <span className="muted" title={t("notMeasured")}>
            —<span className="sr-only"> {t("notMeasured")}</span>
          </span>
        ) : (
          formatRate(item.rate_per_second, locale)
        )}
      </td>
      <td className="traffic-trend-cell">
        <Trend series={item.series} data={data} />
      </td>
      <td className="num">
        {item.peak_rate_per_second === null ? (
          <span className="muted">—</span>
        ) : (
          formatRate(item.peak_rate_per_second, locale)
        )}
        {item.peak_bucket_start && (
          <span className="cell-sub">
            <UtcTime value={item.peak_bucket_start} variant="compact" className="nowrap" />
          </span>
        )}
      </td>
    </>
  );
}

export function TrafficTable({
  view,
  query,
  data,
  sourceId,
}: {
  view: "waf" | "ips";
  query: TrafficQuery;
  data: TrafficPage<WafTrafficItem> | TrafficPage<IpsTrafficItem>;
  sourceId: string;
}) {
  const t = useTranslations("traffic");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const items = data.items as TrafficItemBase[];
  return (
    <TableScroll label={view === "waf" ? t("table.labelWaf") : t("table.labelIps")}>
      <table className={`table-dense traffic-table traffic-table-${view}`}>
        <caption className="sr-only">
          {view === "waf" ? t("table.captionWaf") : t("table.captionIps")}
        </caption>
        <thead>
          <tr>
            <th scope="col" className="num traffic-pos">
              <span aria-hidden="true">{t("table.position")}</span>
              <span className="sr-only">{t("table.positionHint")}</span>
            </th>
            {view === "waf" ? (
              <>
                <SortHeader query={query} column="host" label={t("table.host")} />
                <SortHeader query={query} column="endpoint" label={t("table.endpoint")} />
              </>
            ) : (
              <SortHeader query={query} column="ip" label={t("table.ip")} />
            )}
            <SortHeader query={query} column="rate" label={t("table.rate")} numeric />
            <th scope="col" className="traffic-trend-cell">
              {t("table.trend")}
              <span className="cell-sub">
                {data.series_points <= 1 ? t("table.trendOne") : t("table.trendScale")}
              </span>
            </th>
            <SortHeader query={query} column="peak" label={t("table.peak")} numeric />
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const waf = view === "waf" ? (item as WafTrafficItem) : null;
            const ips = view === "ips" ? (item as IpsTrafficItem) : null;
            return (
              <tr key={item.id} className={item.active ? undefined : "traffic-retired"}>
                <td className="num traffic-pos">{formatCount(data.offset + index + 1, locale)}</td>
                {waf && (
                  <>
                    <td className="cell-name traffic-host">
                      {waf.virtual_host ? (
                        <HostLabel text={waf.virtual_host} />
                      ) : (
                        <span className="muted">{t("table.noHost")}</span>
                      )}
                      <Flags item={item} measured={data.buckets_measured} />
                    </td>
                    <td className="cell-name traffic-path">
                      <Link
                        href={`/behavior/waf/sources/${encodeURIComponent(sourceId)}`}
                        className="traffic-link"
                        title={t("links.wafRow")}
                      >
                        <span className="traffic-method">{waf.http_method}</span>{" "}
                        <PathLabel text={waf.normalized_endpoint ?? ""} />
                      </Link>
                    </td>
                  </>
                )}
                {ips && (
                  <td className="cell-name traffic-host">
                    <Link
                      href={`/anomalies?subject_kind=IPS_ENTITY&ips_entity_id=${encodeURIComponent(ips.id)}`}
                      className="traffic-link traffic-ip"
                      title={t("links.ipsRow")}
                    >
                      {ips.destination_ip}
                    </Link>
                    {ips.display_name && <span className="cell-sub">{ips.display_name}</span>}
                    <Flags item={item} measured={data.buckets_measured} />
                  </td>
                )}
                <MetricCells item={item} data={data} locale={locale} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
