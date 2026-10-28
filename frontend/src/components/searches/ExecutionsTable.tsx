// Execution history of one scheduled search.
//
// A truncated run was cut off at the search's row cap. Its count is therefore
// a lower bound ("≥ 500"), and it cannot say whether the threshold was met, so
// the Threshold column reads "no verdict" for it rather than the "—" that a
// run measured within its threshold shows.

import "@/components/ui/controls.css";

import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { SearchExecution } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import type { FormatLocale } from "@/lib/format";
import { axisTimeLabel, formatRunDuration, resultCount, thresholdVerdict } from "@/lib/searchRuns";

import { versionTag } from "./searchesText";

function execTone(status: string): string {
  if (status === "COMPLETED") return "ok";
  if (status === "FAILED" || status === "TIMEOUT") return "crit";
  return "warn";
}

type Say = ReturnType<typeof useTranslations>;

function Results({ e, t, locale }: { e: SearchExecution; t: Say; locale: FormatLocale }) {
  const count = resultCount(e);
  switch (count.kind) {
    case "lower-bound":
      return (
        <span title={t("lowerBoundNote")}>
          <span aria-hidden="true">≥ </span>
          <span className="sr-only">{t("atLeast")}</span>
          {plainCount(count.value ?? 0, locale)}
        </span>
      );
    case "truncated-unknown":
      return (
        <span className="muted" title={t("lowerBoundNote")}>
          {t("truncatedUnknown")}
        </span>
      );
    case "measured":
      return <>{plainCount(count.value ?? 0, locale)}</>;
    default:
      return <>—</>;
  }
}

function Threshold({ e, t }: { e: SearchExecution; t: Say }) {
  switch (thresholdVerdict(e)) {
    case "breached":
      return <span className="pill crit">{t("breached")}</span>;
    case "no-verdict":
      return (
        <span className="muted" title={t("noVerdictNote")}>
          {t("noVerdict")}
        </span>
      );
    default:
      return <>—</>;
  }
}

export function ExecutionsTable({ executions }: { executions: SearchExecution[] }) {
  // No hook beyond next-intl's: the search page, a server page, renders this table.
  const t = useTranslations("searches.executions");
  const say = asTextTranslator(useTranslations());
  const units = useTranslations("units") as unknown as Parameters<typeof formatRunDuration>[1];
  const locale = useLocale() as FormatLocale;
  const anyTruncated = executions.some((e) => e.truncated);
  return (
    <>
      <TableScroll label={t("label")}>
        <table className="table-dense">
          <caption className="sr-only">{t("caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("started")}</th>
              <th scope="col">{t("status")}</th>
              <th scope="col">{t("trigger")}</th>
              <th scope="col">{t("version")}</th>
              <th scope="col" className="num">{t("duration")}</th>
              <th scope="col" className="num">{t("results")}</th>
              <th scope="col" className="num">{t("retries")}</th>
              <th scope="col">{t("threshold")}</th>
              <th scope="col">{t("error")}</th>
            </tr>
          </thead>
          <tbody>
            {executions.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{axisTimeLabel(e.started_at)}</td>
                <td>
                  <span className={`pill ${execTone(e.status)}`}>
                    {enumLabel(say, "executionStatus", e.status)}
                  </span>
                </td>
                <td>{enumLabel(say, "executionTrigger", e.trigger)}</td>
                <td>{versionTag(e.query_version)}</td>
                <td className="num">{formatRunDuration(e.duration_ms, units, locale)}</td>
                <td className="num">
                  <Results e={e} t={t} locale={locale} />
                </td>
                <td className="num">{plainCount(e.retry_count, locale)}</td>
                <td>
                  <Threshold e={e} t={t} />
                </td>
                <td>{e.error_type ? <span className="pill crit">{e.error_type}</span> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {anyTruncated && (
        <ul className="table-notes" aria-label={t("notesLabel")}>
          <li>{t("noteBound")}</li>
          <li>{t("noteVerdict")}</li>
        </ul>
      )}
    </>
  );
}
