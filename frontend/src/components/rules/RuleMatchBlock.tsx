// "Which rules changed match rate this span?" (SOC-6 design §7.1).
//
// Fed by its own read (`GET /rules/match-anomalies`), not by `RuleStatistics`, so a failure
// here is this block's alone: the five blocks around it keep what they have. The block is
// honest about the span it could judge:
//
//   NOT_COLLECTED      the collector has not run; there is no table, because an empty table
//                      would read as "measured, found nothing".
//   INSUFFICIENT_DATA  the span is not fully measured; silence is not judged, storm rows are
//                      listed with the status of the windows behind them.
//   OK                 the span is complete; an empty list is a measurement.
//
// A null observed or expected count is the dash, never 0. A TRUNCATED row says its count is a
// lower bound.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import "@/components/ui/controls.css";
import "./matchAnomalies.css";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { MatchAnomalyRow, RuleMatchAnomalies } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatCount } from "@/lib/format";
import { useBackendMessage } from "@/i18n/backendMessage";
import { enumLabel } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

import { formatMatches, MISSING, windowWidthText } from "./ruleMatchFormat";

function Figure({ value, lowerBound }: { value: number | null; lowerBound: boolean }) {
  const t = useTranslations("rules.matchBlock");
  const locale = useLocale() as Locale;
  if (value === null) {
    return (
      <span className="muted">
        {MISSING}
        <span className="sr-only"> {t("notRecorded")}</span>
      </span>
    );
  }
  return (
    <>
      {lowerBound ? "≥ " : ""}
      {formatMatches(value, locale)}
    </>
  );
}

function Rows({ items }: { items: MatchAnomalyRow[] }) {
  const t = useTranslations("rules.matchBlock");
  const kinds = useTranslations("enums.ruleAnomalyType");
  const say = asTextTranslator(useTranslations());
  return (
    <TableScroll label={t("label")}>
      <table className="stats-table match-table">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col" className="match-col-rule">
              {t("rule")}
            </th>
            <th scope="col" className="stats-wide-fact match-col-fig">
              {t("detector")}
            </th>
            <th scope="col" className="num match-col-fig">
              {t("observed")}
            </th>
            <th scope="col" className="num match-col-fig">
              {t("expected")}
            </th>
            <th scope="col" className="match-col-state">
              {t("state")}
            </th>
            <th scope="col" className="match-col-time">
              {t("started")}
            </th>
            <th scope="col" className="match-col-act">
              <span className="sr-only">{t("actions")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => {
            const detector = (kinds.has(r.detector) ? kinds(r.detector) : r.detector) as string;
            const truncated = r.row_state === "TRUNCATED";
            return (
              <tr key={r.anomaly_id}>
                <th scope="row" className="cell-rule">
                  <Link href={`/rules/${r.rule_id}`}>{r.name}</Link>
                  <span className="cell-sub">
                    {t("qradarId", { id: r.qradar_id })}
                    {r.is_building_block ? ` · ${t("buildingBlock")}` : ""}
                  </span>
                  <span className="cell-sub stats-narrow-fact">{detector}</span>
                </th>
                <td className="stats-wide-fact">{detector}</td>
                <td className="num">
                  <Figure value={r.observed} lowerBound={truncated} />
                  {truncated && <span className="cell-sub">{t("truncatedRow")}</span>}
                </td>
                <td className="num">
                  <Figure value={r.expected} lowerBound={false} />
                </td>
                <td>
                  <span className={`pill pill-strong ${stateTone(r.state)}`}>
                    {enumLabel(say, "anomalyState", r.state)}
                  </span>
                  {r.window_status !== "complete" && (
                    <span className="cell-sub">
                      {t("windowStatus", {
                        status:
                          r.window_status === "incomplete"
                            ? t("statusIncomplete")
                            : r.window_status === "none"
                              ? t("statusNone")
                              : r.window_status,
                      })}
                    </span>
                  )}
                </td>
                <td>
                  <UtcTime value={r.started} variant="compact" wrapZone />
                </td>
                <td>
                  <Link href={`/anomalies/${r.anomaly_id}`}>{t("open")}</Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function RuleMatchBlock({
  data,
  failed,
}: {
  /** The read's answer; null when it failed. */
  data: RuleMatchAnomalies | null;
  failed: boolean;
}) {
  const t = useTranslations("rules.matchBlock");
  const locale = useLocale() as Locale;
  const backend = useBackendMessage();

  let body;
  if (failed || data === null) {
    body = (
      <div className="notice" role="alert">
        <p>
          <strong>{t("failedTitle")}</strong>
        </p>
        <p>{t("failedHelp")}</p>
      </div>
    );
  } else if (data.state === "NOT_COLLECTED" || data.items === null) {
    const reason =
      data.reason === null ? null : backend({ text: data.reason, code: data.reason_code, params: data.reason_params });
    body = (
      <div className="banner" role="status">
        <div>
          <p>
            <strong>{t("notCollectedTitle")}</strong> {t("notCollectedHelp")}
          </p>
          {reason && <p className="stats-window-reason">{reason}</p>}
        </div>
      </div>
    );
  } else {
    const span = data.span;
    const insufficient = data.state === "INSUFFICIENT_DATA";
    const reason = data.reason ?? span?.reason ?? null;
    const reasonCode = data.reason_code ?? span?.reason_code;
    const reasonParams = data.reason_params ?? span?.reason_params;
    body = (
      <>
        {insufficient && (
          <div className="banner warn" role="status">
            <div>
              <p>
                <strong>{t("insufficientTitle")}</strong> {t("insufficientHelp")}
              </p>
              {reason && (
                <p className="stats-window-reason">
                  {backend({ text: reason, code: reasonCode, params: reasonParams })}
                </p>
              )}
              {span && (
                <p className="match-coverage">
                  {t("coverage", {
                    expected: formatCount(span.expected_windows, locale),
                    complete: formatCount(span.complete_windows, locale),
                    missing: formatCount(span.missing_windows, locale),
                    truncated: formatCount(span.truncated_windows, locale),
                    failed: formatCount(span.failed_windows, locale),
                  })}
                </p>
              )}
            </div>
          </div>
        )}
        {span && (
          <p className="subtitle">
            {t("windowNote", { width: windowWidthText(span.window_seconds), days: span.days })}
          </p>
        )}
        {data.items.length === 0 ? (
          <div className="notice">{insufficient ? t("emptyIncomplete") : t("empty")}</div>
        ) : (
          <>
            <Rows items={data.items} />
            <p className="match-cut">
              {data.items_truncated
                ? t("cut", {
                    shown: formatCount(data.items.length, locale),
                    total: formatCount(data.total, locale),
                  })
                : t("count", { count: data.total ?? data.items.length })}
            </p>
          </>
        )}
      </>
    );
  }

  return (
    <section aria-labelledby="stats-match">
      <div className="section-head">
        <h2 className="section-title" id="stats-match">
          {t("heading")}
        </h2>
      </div>
      <p className="subtitle">{t("question")}</p>
      {body}
    </section>
  );
}
