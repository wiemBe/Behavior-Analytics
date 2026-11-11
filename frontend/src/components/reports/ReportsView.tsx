"use client";

// The reports page: the period controls, then the four blocks of one summary read. The period is
// in the URL, so a link reproduces the report. The controls are drawn in every state; only the
// figures wait for the read.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { ExportCsv } from "@/components/listview/ExportCsv";
import { useBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator } from "@/i18n/textTranslator";
import { alertsCsvUrl, anomaliesCsvUrl, reportsApi, type ReportSummary } from "@/lib/api";
import {
  REPORT_PERIODS,
  calendarStart,
  endsAtNow,
  isCalendar,
  isCurrentPeriod,
  parseReportQuery,
  periodExportParams,
  reportHref,
  stepStart,
  type ReportQuery,
} from "@/lib/reports";
import { stampText } from "@/lib/timeRange";

import { reportLoadReason } from "./loadError";
import { AlertsBlock, AnomaliesBlock, AnomalyClocksBlock, NoiseBlock } from "./ReportBlocks";

import "@/components/ui/controls.css";
import "./reports.css";

type Phase =
  | { status: "loading" }
  | { status: "error"; reason: string }
  | { status: "ready"; summary: ReportSummary };

function PeriodBar({
  query,
  nowMs,
  summary,
}: {
  query: ReportQuery;
  nowMs: number;
  summary: ReportSummary | null;
}) {
  const t = useTranslations("reports.period");
  const { period, start } = query;
  const calendar = isCalendar(period) && start !== null;
  const unit = period === "calendar_month" ? "calendar_month" : "calendar_week";
  const current = calendar && isCurrentPeriod(period, start, nowMs);
  return (
    <div className="rp-bar">
      <div className="range-chips" role="group" aria-label={t("label")}>
        <span className="range-chip-list" role="group" aria-label={t("chipsLabel")}>
          {REPORT_PERIODS.map((p) => (
            <Link
              key={p}
              className="range-chip"
              href={reportHref(p, isCalendar(p) ? calendarStart(p, nowMs) : null, nowMs)}
              aria-current={p === period ? "true" : undefined}
            >
              {t(p)}
            </Link>
          ))}
        </span>
        {calendar && (
          <span className="rp-step" role="group" aria-label={t("stepLabel", { unit: t(`unit.${unit}`) })}>
            <Link className="range-chip" href={reportHref(period, stepStart(period, start, -1), nowMs)}>
              {t(`previous.${unit}`)}
            </Link>
            <span className="rp-step-label">
              {t(unit === "calendar_month" ? "startingMonth" : "startingWeek", {
                date: start.slice(0, 10),
              })}
            </span>
            {current ? (
              <span className="range-chip rp-off" aria-disabled="true" title={t("nextOff")}>
                {t(`next.${unit}`)}
              </span>
            ) : (
              <Link className="range-chip" href={reportHref(period, stepStart(period, start, 1), nowMs)}>
                {t(`next.${unit}`)}
              </Link>
            )}
          </span>
        )}
      </div>
      {summary && (
        <p className="rp-range" role="group" aria-label={t("rangeLabel")}>
          {t(endsAtNow(summary) ? "rangeNow" : "range", {
            from: stampText(summary.start),
            to: stampText(summary.end),
          })}
        </p>
      )}
    </div>
  );
}

function Links() {
  const t = useTranslations("reports.links");
  return (
    <section className="rp-block" aria-labelledby="rp-links">
      <h2 id="rp-links" className="rp-h">
        {t("title")}
      </h2>
      <div className="rp-links">
        <p>
          <Link href="/behavior/noise">{t("noise")}</Link> <span className="muted">— {t("noiseHint")}</span>
        </p>
        <p>
          <Link href="/coverage">{t("coverage")}</Link> <span className="muted">— {t("coverageHint")}</span>
        </p>
      </div>
    </section>
  );
}

export function ReportsView({ period, start }: { period?: string; start?: string }) {
  const t = useTranslations("reports");
  const say = asTextTranslator(useTranslations());
  const backendMessage = useBackendMessage();
  const [phase, setPhase] = useState<Phase>({ status: "loading" });
  const [nonce, setNonce] = useState(0);

  // The clock is read once per request, so the calendar boundaries do not move while one is shown.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const nowMs = useMemo(() => Date.now(), [period, start, nonce]);
  const query = useMemo(() => parseReportQuery({ period, start }, nowMs), [period, start, nowMs]);

  useEffect(() => {
    let cancelled = false;
    setPhase({ status: "loading" });
    reportsApi
      .summary(query.period, query.start ?? undefined)
      .then((summary) => {
        if (!cancelled) setPhase({ status: "ready", summary });
      })
      .catch((err: unknown) => {
        if (!cancelled) setPhase({ status: "error", reason: reportLoadReason(err, say, backendMessage) });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.period, query.start, nonce]);

  const summary = phase.status === "ready" ? phase.summary : null;
  const range = summary ? { start: summary.start, end: summary.end } : null;
  const empty =
    summary !== null &&
    summary.alerts.rows?.length === 0 &&
    summary.anomalies.rows?.length === 0;

  return (
    <div className="rp">
      {query.issues.length > 0 && (
        <p className="banner warn" role="status">
          {query.issues.map((issue) => t(`ignored.${issue}`)).join(" ")}
        </p>
      )}
      <PeriodBar query={query} nowMs={nowMs} summary={summary} />

      {phase.status === "loading" && (
        <p className="notice" role="status" aria-live="polite">
          {t("loading")}
        </p>
      )}

      {phase.status === "error" && (
        <div className="notice" role="alert">
          <p>
            <strong>{t("loadFailed.title")}</strong>
          </p>
          <p>{phase.reason}</p>
          <p>
            <button type="button" className="action secondary" onClick={() => setNonce((n) => n + 1)}>
              {t("retry")}
            </button>
          </p>
        </div>
      )}

      {summary && range && empty && (
        <>
          <div className="notice" role="status">
            <p>{t("empty.text")}</p>
            <p className="muted">
              {t(endsAtNow(summary) ? "empty.rangeNow" : "empty.range", {
                from: stampText(summary.start),
                to: stampText(summary.end),
              })}
            </p>
          </div>
          <div className="rp-empty-exports">
            <ExportCsv href={alertsCsvUrl(periodExportParams(range))} label={t("export.alerts")} />
            <ExportCsv href={anomaliesCsvUrl(periodExportParams(range))} label={t("export.anomalies")} />
          </div>
        </>
      )}

      {summary && range && !empty && (
        <>
          <AlertsBlock block={summary.alerts} range={range} />
          <AnomaliesBlock block={summary.anomalies} range={range} />
          <AnomalyClocksBlock block={summary.anomaly_clocks} />
          <NoiseBlock block={summary.noise_by_detector} />
        </>
      )}

      <Links />
    </div>
  );
}
