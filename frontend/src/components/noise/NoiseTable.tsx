// The ranked table: one row per log source that raised an anomaly in the window.
//
// Honesty in the cells. A count that was not collected is "not measured", never 0. A source that is
// not monitored says "not monitored" across its figures. A source without a reliable baseline is
// marked "Not judged" with the backend's reason, so its count is never read as calm. The noise share
// is shown only when the backend gives one; with a short denominator the row says `n noise / m closed`.

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { useEnumLabels } from "@/i18n/enumLabels";
import type { NoiseSourceRow, NoiseSourcesPage } from "@/lib/api";
import { formatCount, formatShare, type FormatLocale } from "@/lib/format";
import {
  MIN_NOISE_DENOMINATOR,
  isJudged,
  isNotMonitored,
  noiseDetailHref,
  noiseHref,
  sourceAlertsHref,
  sourceHref,
  type NoiseQuery,
} from "@/lib/noise";
import type { NoiseOrder } from "@/lib/api";

import "@/components/ui/controls.css";
import "./noise.css";

function Missing() {
  const t = useTranslations("noise.table");
  return (
    <span className="muted" title={t("notMeasured")}>
      —<span className="sr-only"> {t("notMeasured")}</span>
    </span>
  );
}

function Count({ value, locale }: { value: number | null; locale: FormatLocale }) {
  return value === null ? <Missing /> : <>{formatCount(value, locale)}</>;
}

function SortHeader({
  query,
  order,
  label,
  unit,
  sortable = true,
}: {
  query: NoiseQuery;
  order: NoiseOrder;
  label: string;
  unit: string;
  sortable?: boolean;
}) {
  const t = useTranslations("noise.table");
  const active = query.order === order;
  const body = (
    <>
      {label}
      <span className="cell-sub">{unit}</span>
    </>
  );
  return (
    <th scope="col" className="num" aria-sort={active ? "descending" : "none"}>
      {sortable ? (
        <Link
          href={noiseHref(query, { order, offset: 0 })}
          className="sort-link"
          title={t("orderBy", { name: label })}
        >
          <span>{body}</span>
          <span aria-hidden="true">{active ? " ↓" : ""}</span>
        </Link>
      ) : (
        body
      )}
    </th>
  );
}

function SourceCell({ row }: { row: NoiseSourceRow }) {
  const t = useTranslations("noise.table");
  const { label } = useEnumLabels();
  // 17b: noiseDetailHref returns the tuning page once /behavior/noise/[id] exists.
  const detail = noiseDetailHref(row);
  return (
    <td className="cell-name noise-source">
      <Link href={sourceHref(row)}>{row.name}</Link>
      {detail && (
        <>
          {" "}
          <Link href={detail}>{t("detailLink")}</Link>
        </>
      )}
      <span className="cell-sub">
        {[
          row.instance_name,
          label("severity", row.criticality),
          row.top_detector ? t("detector", { name: label("anomalyType", row.top_detector).toLowerCase() }) : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </span>
    </td>
  );
}

function NoiseCell({ row, locale }: { row: NoiseSourceRow; locale: FormatLocale }) {
  const t = useTranslations("noise.table");
  const { noise_closed: noise, noise_closed_total: closed, noise_share: share } = row;
  if (noise === null || closed === null) return <Missing />;
  if (closed === 0) return <span className="muted">{t("noClosed")}</span>;
  if (share === null) {
    return (
      <>
        <span className="nowrap">
          {t("shortLine", { noise: formatCount(noise, locale), closed: formatCount(closed, locale) })}
        </span>
        <span className="cell-sub">{t("shortHint", { min: MIN_NOISE_DENOMINATOR })}</span>
      </>
    );
  }
  return (
    <>
      {formatShare(share, locale)}
      <span className="cell-sub">
        {t("shareLine", { noise: formatCount(noise, locale), closed: formatCount(closed, locale) })}
      </span>
    </>
  );
}

function WorstCell({ row, locale }: { row: NoiseSourceRow; locale: FormatLocale }) {
  const t = useTranslations("noise.table");
  if (row.worst_ratio === null) return <Missing />;
  const ratio = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(row.worst_ratio);
  return (
    <>
      {t("ratio", { value: ratio })}
      {row.worst_z !== null && (
        <span className="cell-sub">
          {t("z", {
            value: new Intl.NumberFormat("en-US", {
              minimumFractionDigits: 1,
              maximumFractionDigits: 1,
            }).format(row.worst_z),
          })}
        </span>
      )}
    </>
  );
}

function JudgedCell({ row }: { row: NoiseSourceRow }) {
  const t = useTranslations("noise.table");
  if (isJudged(row)) {
    return (
      <td className="noise-judged">
        <span className="pill pill-quiet">{t("judgedYes")}</span>
      </td>
    );
  }
  return (
    <td className="noise-judged">
      <span className="pill pill-quiet warn">{t("notJudged")}</span>
      <span className="cell-sub">{row.reason?.text ?? t("notJudgedHint")}</span>
    </td>
  );
}

export function NoiseTable({ query, data }: { query: NoiseQuery; data: NoiseSourcesPage }) {
  const t = useTranslations("noise");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const { label } = useEnumLabels();
  const days = query.window;
  return (
    <TableScroll label={t("table.label")}>
      <table className="table-dense noise-table">
        <caption className="sr-only">{t("table.caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("table.source")}</th>
            <th scope="col">{t("table.class")}</th>
            {days === 7 ? (
              <SortHeader query={query} order="incidents" label={t("table.incidents7")} unit={t("table.unit7")} />
            ) : (
              <th scope="col" className="num noise-window-off">
                {t("table.incidents7")}
                <span className="cell-sub">{t("table.unit7")}</span>
              </th>
            )}
            {days === 28 ? (
              <SortHeader query={query} order="incidents" label={t("table.incidents28")} unit={t("table.unit28")} />
            ) : (
              <th scope="col" className="num noise-window-off">
                {t("table.incidents28")}
                <span className="cell-sub">{t("table.unit28")}</span>
              </th>
            )}
            <SortHeader
              query={query}
              order="alerts"
              label={t("table.alerts")}
              unit={t("table.unitWindow", { days })}
            />
            <SortHeader
              query={query}
              order="noise_share"
              label={t("table.noise")}
              unit={`${t("table.unit28")} · ${t("table.unitClosed")}`}
            />
            <th scope="col" className="num">
              {t("table.worst")}
              <span className="cell-sub">
                {t("table.unit28")} · {t("table.unitRatio")}
              </span>
            </th>
            <th scope="col">{t("table.judged")}</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((row) => {
            const off = isNotMonitored(row);
            const alerts = days === 7 ? row.alerts_7d : row.alerts_28d;
            return (
              <tr key={row.log_source_id}>
                <SourceCell row={row} />
                <td className="noise-class">
                  {row.source_class ? (
                    label("sourceClass", row.source_class)
                  ) : (
                    <span className="muted">{t("table.noClassification")}</span>
                  )}
                </td>
                {off ? (
                  <td className="noise-unmonitored" colSpan={5} title={t("table.notMonitoredHint")}>
                    {t("table.notMonitored")}
                  </td>
                ) : (
                  <>
                    <td className="num">
                      <Count value={row.incidents_7d} locale={locale} />
                    </td>
                    <td className="num">
                      <Count value={row.incidents_28d} locale={locale} />
                    </td>
                    <td className="num">
                      {alerts !== null && alerts > 0 ? (
                        <Link
                          href={sourceAlertsHref(row)}
                          aria-label={t("table.alertsLink", { name: row.name })}
                        >
                          {formatCount(alerts, locale)}
                        </Link>
                      ) : (
                        <Count value={alerts} locale={locale} />
                      )}
                    </td>
                    <td className="num">
                      <NoiseCell row={row} locale={locale} />
                    </td>
                    <td className="num">
                      <WorstCell row={row} locale={locale} />
                    </td>
                  </>
                )}
                <JudgedCell row={row} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
