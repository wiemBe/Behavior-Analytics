// Behavioral overview: an operational landing page.
//
// The question this page answers is "what needs me now?", and the previous
// version answered it badly — not by omitting anything, but by saying
// everything at once. Ten equally weighted counters, then three tables, the
// last of which listed all 59 monitored sources, 55 of them quiet and normal.
// The rows that required action were outnumbered roughly fourteen to one by
// rows that did not, and sat below them.
//
// So: four counters, a worklist, what changed recently, and a proportion. The
// inventory moved to /behavior/sources, where an analyst goes deliberately.
//
// The counter that still matters most is `insufficient_data_sources`. A source
// with no adequate baseline is not being judged at all, and a dashboard
// reporting "0 anomalies" over a fleet of unbaselined sources is reporting the
// absence of detection as the absence of problems. It keeps a primary card,
// its own neutral tone — never green — and an explicit sentence.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { StatCard } from "@/components/StatCard";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  ApiError,
  api,
  sevTone,
  type AnomalySummary,
  type BehaviorSummary,
  type Page,
  type SourceBehavior,
} from "@/lib/api";
import {
  buildAttentionRows,
  countHighDeviation,
  healthDistribution,
  healthSummaryText,
} from "@/lib/behaviorOverview";
import { detectorLabel, formatMetric, formatRatio, stateTone } from "@/lib/behavior";
import { getBehaviorI18n, getEnumLabels } from "@/lib/behaviorI18n.server";
import { plainCount } from "@/lib/figureText";
import { sourcesAsOf } from "@/lib/home/asOf";
import { UtcTime } from "@/components/ui/UtcTime";
import { withRouteMessages } from "@/i18n/RouteMessages";


import "./overview.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("behavior.overview");
  return { title: t("metaTitle") };
}

/** The state's own separator, with a break opportunity after each one. */
const UNDERSCORE = "_";

/** How many rows the worklist and the recent list show before linking on. */
const ATTENTION_LIMIT = 8;
const RECENT_LIMIT = 6;

async function BehaviorPage() {
  const t = await getTranslations("behavior.overview");
  const errors = await getTranslations("behavior.errors");
  const i18n = await getBehaviorI18n();
  const { locale } = i18n;
  const { statePill, severityPill } = await getEnumLabels();
  const count = (n: number) => plainCount(n, locale);
  let summary: BehaviorSummary | null = null;
  let sources: SourceBehavior[] = [];
  let active: AnomalySummary[] = [];
  let activePage: Page<AnomalySummary> | null = null;
  let activeFailed = false;
  let error: string | null = null;

  try {
    // The active-anomaly list supplies the detector type, severity and
    // evidence status the worklist ranks on; its failure must not blank the
    // page, so it is tolerated separately.
    const [summaryResult, sourcesResult, activeResult] = await Promise.all([
      api.behaviorSummary(),
      api.sourceBehaviors(),
      // subject_kind pins the list to Phase A: an unfiltered call would also
      // return WAF anomalies, which this worklist renders in Phase A rows.
      api
        .anomalies({ active_only: true, limit: 200, subject_kind: "LOG_SOURCE" })
        .catch(() => null),
    ]);
    summary = summaryResult;
    sources = sourcesResult;
    activePage = activeResult;
    active = activePage?.items ?? [];
    activeFailed = activePage === null;
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? errors("forbidden")
        : err instanceof ApiError && err.status === 401
          ? errors("expired")
          : t("failed");
  }

  if (error || !summary) {
    return (
      <>
        <PageHeader guide={guideFor("/behavior")} title={t("title")} />
        <div className="notice" role="alert">
          {error ?? t("failedShort")}
        </div>
      </>
    );
  }

  const allActive = (chunks: ReactNode) => <Link href="/anomalies?active_only=true">{chunks}</Link>;
  const unjudged = summary.insufficient_data_sources;
  const highDeviation = countHighDeviation(sources);
  const attention = buildAttentionRows(sources, active, i18n);
  const groups = healthDistribution(sources);
  const recent = summary.recently_resolved.slice(0, RECENT_LIMIT);

  return (
    <>
      <ReportDataAge iso={sourcesAsOf(sources, null)} />
      <PageHeader
        guide={guideFor("/behavior")}
        title={t("title")}
        description={t.rich("description", {
          shown: count(summary.monitored_sources),
          utc: (chunks) => <span className="when-utc">{chunks}</span>,
        })}
      />

      {/* --- level 1: the four counters that decide whether to keep reading -- */}
      <div className="grid-4">
        <StatCard
          label={t("stats.openAnomalies")}
          value={count(summary.open_anomalies)}
          tone={summary.open_anomalies > 0 ? "crit" : undefined}
        />
        <StatCard
          label={t("stats.silentSources")}
          value={count(summary.silent_sources)}
          tone={summary.silent_sources > 0 ? "crit" : undefined}
        />
        <StatCard
          label={t("stats.highDeviation")}
          value={count(highDeviation)}
          tone={highDeviation > 0 ? "warn" : undefined}
          note={t("stats.highDeviationNote")}
        />
        {/* Neutral tone, never green: this is an observability gap, and
            colouring it as healthy is the failure mode this card exists to
            prevent. */}
        <StatCard
          label={t("stats.insufficientData")}
          value={count(unjudged)}
          note={t("stats.insufficientNote")}
        />
      </div>

      {/* --- level 2: the secondary counts, as one compact strip ------------ */}
      <dl className="status-strip">
        <div>
          <dt>{t("strip.spikes")}</dt>
          <dd className="num">{count(summary.spikes)}</dd>
        </div>
        <div>
          <dt>{t("strip.drops")}</dt>
          <dd className="num">{count(summary.drops)}</dd>
        </div>
        <div>
          <dt>{t("strip.candidates")}</dt>
          <dd className="num">{count(summary.candidates)}</dd>
        </div>
        <div>
          <dt>{t("strip.recovering")}</dt>
          <dd className="num">{count(summary.recovering)}</dd>
        </div>
        <div>
          <dt>{t("strip.resolved24h")}</dt>
          <dd className="num">{count(summary.resolved_last_24h)}</dd>
        </div>
        <div>
          <dt>{t("strip.evidencePending")}</dt>
          <dd className="num">{count(summary.evidence_pending)}</dd>
        </div>
        <div>
          <dt>{t("strip.evidenceFailed")}</dt>
          <dd className={summary.evidence_failed > 0 ? "num crit-text" : "num"}>
            {count(summary.evidence_failed)}
          </dd>
        </div>
      </dl>

      {unjudged > 0 && (
        <p className="banner">
          <span>
            <strong>
              {t("banner.lead", {
                count: unjudged,
                shown: count(unjudged),
                total: count(summary.monitored_sources),
              })}
            </strong>{" "}
            {t("banner.rest", { count: unjudged })}
          </span>
        </p>
      )}

      {/* --- needs attention ------------------------------------------------ */}
      <section>
        <h2 className="section-title">{t("attention.title")}</h2>
        {activeFailed && (
          <p className="notice" role="alert">
            {t("attention.activeFailed")}
          </p>
        )}
        {activePage && activePage.total > active.length && (
          <p className="subtitle">
            {t.rich("attention.ranked", {
              shown: count(active.length),
              total: count(activePage.total),
              link: allActive,
            })}
          </p>
        )}
        {attention.length === 0 ? (
          summary.open_anomalies > 0 ? (
            <div className="notice" role="status">
              {t.rich("attention.unmatched", {
                count: summary.open_anomalies,
                shown: count(summary.open_anomalies),
                link: allActive,
              })}
            </div>
          ) : activeFailed ? null : (
            <div className="notice">
              {t("attention.empty")}
            </div>
          )
        ) : (
          <>
            <TableScroll label={t("attention.label")}>
              <table className="attention-table">
                <caption className="sr-only">
                  {t("attention.caption")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t("attention.source")}</th>
                    <th scope="col">{t("attention.state")}</th>
                    <th scope="col">{t("attention.issue")}</th>
                    <th scope="col" className="num">
                      {t("attention.observedExpected")}
                    </th>
                    <th scope="col" className="num">
                      {t("attention.deviation")}
                    </th>
                    <th scope="col" className="num">
                      {t("attention.started")}
                    </th>
                    <th scope="col">
                      <span className="sr-only">{t("attention.detail")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {attention.slice(0, ATTENTION_LIMIT).map((row) => (
                    <tr key={row.key}>
                      <td className="cell-name cell-name-single" title={row.sourceName}>
                        <Link href={`/behavior/sources/${row.sourceId}`}>
                          {row.sourceName}
                        </Link>
                      </td>
                      <td>
                        <span className={`pill pill-wrap ${stateTone(row.state)}`}>
                          {/* A break opportunity after each underscore, so
                              INSUFFICIENT_DATA can fold in a narrow column. */}
                          <span>
                            {statePill(row.state)
                              .split("_")
                              .map((part, i, all) => (
                                <Fragment key={i}>
                                  {part}
                                  {i < all.length - 1 && (
                                    <>
                                      {UNDERSCORE}
                                      <wbr />
                                    </>
                                  )}
                                </Fragment>
                              ))}
                          </span>
                        </span>
                      </td>
                      <td>
                        {/* Label and badge are separate elements: the label
                            wraps, the badge does not, and neither squeezes
                            the other. */}
                        <span className="issue-with-severity">
                          <span className="issue-with-severity__label">
                            {row.issue}
                          </span>
                          {row.severity && (
                            <span className={`pill pill-quiet ${sevTone(row.severity)}`}>
                              {severityPill(row.severity)}
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="num">
                        {/* Neither value measured: one dash, not "— / —". An
                            unbaselined source has no expectation; an em dash
                            says so rather than inventing a zero. */}
                        {row.observed == null && row.expected == null ? (
                          <span className="muted">—</span>
                        ) : (
                          <>
                            {formatMetric(row.observed, 2, locale)}
                            {" / "}
                            {row.expected == null ? (
                              <span className="muted">—</span>
                            ) : (
                              formatMetric(row.expected, 2, locale)
                            )}
                          </>
                        )}
                      </td>
                      <td className="num">{formatRatio(row.deviation, locale)}</td>
                      <td className="num cell-when">
                        <UtcTime value={row.at} variant="compact" wrapZone />
                      </td>
                      <td>
                        <Link href={row.href}>
                          {row.href.startsWith("/anomalies")
                            ? t("attention.investigate")
                            : t("attention.detail")}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
            {attention.length > ATTENTION_LIMIT && (
              <p className="subtitle">
                {t.rich("attention.showing", {
                  limit: count(ATTENTION_LIMIT),
                  total: count(attention.length),
                  link: allActive,
                })}
              </p>
            )}
          </>
        )}
      </section>

      {/* --- recently resolved ---------------------------------------------- */}
      <section>
        <h2 className="section-title">{t("recent.title")}</h2>
        {recent.length === 0 ? (
          <div className="notice">{t("recent.empty")}</div>
        ) : (
          <>
            {summary.recently_resolved.length > RECENT_LIMIT && (
              <p className="subtitle">
                {t("recent.latest", {
                  shown: count(recent.length),
                  total: count(summary.recently_resolved.length),
                })}
              </p>
            )}
            <TableScroll label={t("recent.label")}>
              <table>
                <caption className="sr-only">
                  {t("recent.caption")}
                </caption>
                <thead>
                  <tr>
                    <th scope="col">{t("recent.source")}</th>
                    <th scope="col">{t("recent.detector")}</th>
                    <th scope="col">{t("recent.severity")}</th>
                    <th scope="col" className="num">
                      {t("recent.resolved")}
                    </th>
                    <th scope="col">
                      <span className="sr-only">{t("recent.detail")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((a) => (
                    <tr key={a.id}>
                      <td className="cell-name">{a.log_source_name ?? a.log_source_id}</td>
                      <td>{detectorLabel(a.anomaly_type, i18n)}</td>
                      <td>
                        <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                          {severityPill(a.severity)}
                        </span>
                      </td>
                      <td className="num">
                        {<UtcTime value={a.resolved_at} variant="compact" />}
                      </td>
                      <td>
                        <Link href={`/anomalies/${a.id}`}>{t("recent.investigate")}</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          </>
        )}
      </section>

      {/* --- fleet proportion ------------------------------------------------ */}
      <section>
        <h2 className="section-title">{t("health.title")}</h2>
        <HealthDistribution
          groups={groups}
          total={sources.length}
          summary={healthSummaryText(groups, sources.length, summary.silent_sources, i18n.t)}
          say={i18n.t}
        />
        <p className="subtitle">
          <Link href="/behavior/sources">{t("health.viewAll", { shown: count(sources.length) })}</Link>
        </p>
      </section>
    </>
  );
}

export default withRouteMessages("/behavior", BehaviorPage);
