// The per-detector counts of one source: what it raised in 7 and 28 days, how much of that was
// closed as noise, and the worst deviation. The first row is the source as a whole; the rows below
// are its detectors. A count that was not collected is "not measured", never 0 (the 17a row rules).

import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { useEnumLabels } from "@/i18n/enumLabels";
import type { NoiseDetectorRow, NoiseSourceDetail } from "@/lib/api";
import type { FormatLocale } from "@/lib/format";

import { Count, NoiseCell, WorstCell } from "./NoiseTable";

import "@/components/ui/controls.css";
import "./noise.css";

type Counts = Pick<
  NoiseDetectorRow,
  | "incidents_7d"
  | "incidents_28d"
  | "alerts_7d"
  | "alerts_28d"
  | "noise_closed"
  | "noise_closed_total"
  | "noise_share"
  | "worst_ratio"
  | "worst_z"
>;

function CountCells({ row, locale }: { row: Counts; locale: FormatLocale }) {
  return (
    <>
      <td className="num">
        <Count value={row.incidents_7d} locale={locale} />
      </td>
      <td className="num">
        <Count value={row.incidents_28d} locale={locale} />
      </td>
      <td className="num">
        <Count value={row.alerts_7d} locale={locale} />
      </td>
      <td className="num">
        <Count value={row.alerts_28d} locale={locale} />
      </td>
      <td className="num">
        <NoiseCell row={row} locale={locale} />
      </td>
      <td className="num">
        <WorstCell row={row} locale={locale} />
      </td>
    </>
  );
}

export function NoiseDetectorCounts({ detail }: { detail: NoiseSourceDetail }) {
  const t = useTranslations("noise.detail");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const { label } = useEnumLabels();
  return (
    <div>
      <TableScroll label={t("countsLabel")}>
        <table className="table-dense noise-table noise-counts">
          <caption className="sr-only">{t("countsCaption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("detector")}</th>
              <th scope="col" className="num">
                {t("incidents")}
                <span className="cell-sub">{t("unit7")}</span>
              </th>
              <th scope="col" className="num">
                {t("incidents")}
                <span className="cell-sub">{t("unit28")}</span>
              </th>
              <th scope="col" className="num">
                {t("alerts")}
                <span className="cell-sub">{t("unit7")}</span>
              </th>
              <th scope="col" className="num">
                {t("alerts")}
                <span className="cell-sub">{t("unit28")}</span>
              </th>
              <th scope="col" className="num">
                {t("noise")}
                <span className="cell-sub">
                  {t("unit28")} · {t("unitClosed")}
                </span>
              </th>
              <th scope="col" className="num">
                {t("worst")}
                <span className="cell-sub">
                  {t("unit28")} · {t("unitRatio")}
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr className="noise-total-row">
              <th scope="row">{t("allDetectors")}</th>
              <CountCells row={detail} locale={locale} />
            </tr>
            {detail.detectors.map((d) => (
              <tr key={d.anomaly_type}>
                <th scope="row">{label("anomalyType", d.anomaly_type)}</th>
                <CountCells row={d} locale={locale} />
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {detail.detectors.length === 0 && <p className="noise-footnote">{t("noDetectors")}</p>}
      <p className="noise-footnote">{t("countsFootnote")}</p>
    </div>
  );
}
