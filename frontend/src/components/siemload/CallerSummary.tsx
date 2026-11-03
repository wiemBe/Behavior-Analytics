"use client";

// The per-caller summary of the searches recorded in one window: one row per caller. Percentiles
// are over completed searches only; totals cover every finished search. A total that leaves out
// searches QRadar gave no figure for is a lower bound and says so (≥); one with no figure at all
// is a dash.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { LoadingState } from "@/components/ui/RouteState";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { arielLoadApi, type ArielCallerSummary } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { asTextTranslator } from "@/i18n/textTranslator";

import { callerLabel } from "./callers";
import {
  DEFAULT_TREND,
  formatMs,
  rangeHours,
  rangeHref,
  SUMMARY_RANGES,
  type SummaryRangeId,
  type TrendRangeId,
} from "./siemFormat";

const RANGE_LABEL = {
  "1h": "range1h",
  "24h": "range24h",
  "7d": "range7d",
  "30d": "range30d",
} as const;

/**
 * The failed outcomes: errors, cancellations, timeouts, abandoned searches (their worker stopped
 * before it recorded an end) and searches that found Ariel unavailable.
 */
function failedOf(row: ArielCallerSummary): number {
  return (
    (row.outcomes.ERROR ?? 0) +
    (row.outcomes.CANCELED ?? 0) +
    (row.outcomes.TIMEOUT ?? 0) +
    (row.outcomes.ABANDONED ?? 0) +
    (row.outcomes.PROVIDER_UNAVAILABLE ?? 0)
  );
}

/** A total over the searches that reported a figure: a lower bound when some did not. */
function Total({
  sum,
  unknown,
  render,
}: {
  sum: number | null;
  unknown: number;
  render: (value: number) => string;
}) {
  const t = useTranslations("siemLoad.summary");
  if (sum === null) return <>—</>;
  return (
    <>
      {unknown > 0 ? "≥ " : null}
      {render(sum)}
      {unknown > 0 ? <span className="cell-sub">{t("unreported", { count: unknown })}</span> : null}
    </>
  );
}

function RangeControl({ range, trend }: { range: SummaryRangeId; trend: TrendRangeId }) {
  const t = useTranslations("siemLoad.summary");
  return (
    <nav className="range-selector" aria-label={t("rangeLabel")}>
      {SUMMARY_RANGES.map((choice) => (
        <Link
          key={choice.id}
          href={rangeHref(choice.id, trend)}
          className={`range-option${choice.id === range ? " range-option-active" : ""}`}
          aria-current={choice.id === range ? "page" : undefined}
        >
          {t(RANGE_LABEL[choice.id])}
        </Link>
      ))}
    </nav>
  );
}

function Body({ range }: { range: SummaryRangeId }) {
  const t = useTranslations("siemLoad.summary");
  const errors = useTranslations("errors");
  const state = useTranslations("siemLoad.state");
  const callers = asTextTranslator(useTranslations("siemLoad.caller"));
  const hours = rangeHours(range);
  const resource = useApiResource(`/admin/ariel-load?hours=${hours}`, (signal) =>
    arielLoadApi.summary(hours, signal),
  );

  if (resource.status === "idle" || resource.status === "loading") {
    return <LoadingState what={t("loadingWhat")} />;
  }
  if (resource.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("couldNotRead")}</strong>
        </p>
        <p>{t.rich("forbidden", { perm: (chunks) => <code className="perm">{chunks}</code> })}</p>
      </div>
    );
  }
  if (resource.status === "error") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("couldNotRead")}</strong>
        </p>
        <p>
          {resource.httpStatus === 429
            ? resource.retryAfter == null
              ? errors("rateLimited")
              : errors("rateLimitedAfter", { seconds: resource.retryAfter })
            : resource.httpStatus === null
              ? state("noAnswer")
              : state("answeredWith", { status: resource.httpStatus })}
        </p>
        <p>{state("advice")}</p>
      </div>
    );
  }

  const summary = resource.data;
  if (summary.empty || summary.callers.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("emptyTitle", { range: t(RANGE_LABEL[range]) })}</strong>
        </p>
        <p>{t("emptyHelp")}</p>
      </div>
    );
  }

  return (
    <>
      <p className="sl-asof">
        {t.rich("window", {
          from: () => <UtcTime value={summary.start} />,
          to: () => <UtcTime value={summary.end} />,
        })}
      </p>
      <TableScroll label={t("label")}>
        <table className="sl-table">
          <caption className="sr-only">{t("label")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("caller")}</th>
              <th scope="col" className="num">
                {t("searches")}
              </th>
              <th scope="col" className="num">
                {t("completed")}
              </th>
              <th scope="col" className="num">
                {t("failed")}
              </th>
              <th scope="col" className="num">
                {t("refused")}
              </th>
              <th scope="col" className="num">
                {t("timeouts")}
              </th>
              <th scope="col" className="num">
                {t("wallP50")}
              </th>
              <th scope="col" className="num">
                {t("wallP95")}
              </th>
              <th scope="col" className="num">
                {t("executionSum")}
              </th>
              <th scope="col" className="num">
                {t("executionP95")}
              </th>
              <th scope="col" className="num">
                {t("records")}
              </th>
            </tr>
          </thead>
          <tbody>
            {summary.callers.map((row) => (
              <tr key={row.caller}>
                <th scope="row">{callerLabel(callers, row.caller)}</th>
                <td className="num">{formatCount(row.searches)}</td>
                <td className="num">{formatCount(row.outcomes.COMPLETED ?? 0)}</td>
                <td className="num">{formatCount(failedOf(row))}</td>
                <td className="num">{formatCount(row.outcomes.REFUSED ?? 0)}</td>
                <td className="num">{formatCount(row.outcomes.TIMEOUT ?? 0)}</td>
                <td className="num">{formatMs(row.wall_ms_p50)}</td>
                <td className="num">{formatMs(row.wall_ms_p95)}</td>
                <td className="num">
                  <Total
                    sum={row.qradar_execution_ms_sum}
                    unknown={row.qradar_execution_ms_unknown}
                    render={formatMs}
                  />
                </td>
                <td className="num">{formatMs(row.qradar_execution_ms_p95)}</td>
                <td className="num">
                  <Total
                    sum={row.records_returned_sum}
                    unknown={row.records_returned_unknown}
                    render={(value) => formatCount(value)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <p className="sl-hint">{t("note")}</p>
    </>
  );
}

export function CallerSummary({
  range,
  trend = DEFAULT_TREND,
}: {
  range: SummaryRangeId;
  trend?: TrendRangeId;
}) {
  const t = useTranslations("siemLoad.summary");
  return (
    <section className="sl-section" aria-labelledby="sl-summary-h">
      <div className="sl-section-head">
        <h2 className="sl-h" id="sl-summary-h">
          {t("heading")}
        </h2>
        <RangeControl range={range} trend={trend} />
      </div>
      <Body range={range} />
    </section>
  );
}
