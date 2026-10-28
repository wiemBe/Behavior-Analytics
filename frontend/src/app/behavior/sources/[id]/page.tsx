// Log-source behavior detail.
//
// The page answers six questions in order: is this source behaving normally
// now, what is its observed against expected volume, when did it last send an
// event, has it produced anomalies recently, is its baseline trustworthy, and
// is collection healthy. Technical detail comes last and starts collapsed.
//
// The chart's correctness condition is negative, and it is the reason this
// page exists in the shape it does: an interval that was not collected must
// not be drawn as traffic, as zero, or as a line connecting the observations
// either side of it. All three would turn a collector outage into an apparent
// source outage. A COMPLETE bucket reporting zero is the opposite case — a
// real measurement of silence — and must be drawn as zero, not as a gap.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { RangeSelector } from "@/components/behavior/RangeSelector";
import { SourceAdvanced } from "@/components/behavior/SourceAdvanced";
import { SourceAnomalies } from "@/components/behavior/SourceAnomalies";
import { SourceEvaluationNotice } from "@/components/behavior/SourceEvaluationNotice";
import { SourceHeader } from "@/components/behavior/SourceHeader";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { SourceQuality } from "@/components/behavior/SourceQuality";
import { VolumeChart } from "@/components/behavior/VolumeChart";
import {
  ApiError,
  api,
  type AnomalySummary,
  type BaselineCell,
  type LogSourceDetail,
  type MetricBucket,
  type Page,
  type SourceBehavior,
} from "@/lib/api";
import {
  detectorLabel,
  formatMetric,
  formatRatio,
  isUnjudged,
  stateMeaningAgainst,
} from "@/lib/behavior";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { figureNewerThanVerdict } from "@/lib/evaluationFault";
import {
  METRIC_LIMIT,
  baselineQuality,
  collectionHealth,
  parseRange,
  partitionAnomalies,
  rangeLabel,
  summarizeSourceTimeline,
} from "@/lib/sourceDetail";
import { buildSeries } from "@/lib/timeseries";
import { UtcTime } from "@/components/ui/UtcTime";


import "../sources.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("behavior.sourcePage");
  return { title: t("metaTitle") };
}

export default async function SourceBehaviorPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const t = await getTranslations("behavior.sourcePage");
  const errors = await getTranslations("behavior.errors");
  const i18n = await getBehaviorI18n();
  const { locale } = i18n;
  const range = parseRange(query.range);
  const from7d = query.from === "7d";
  const until = new Date().toISOString();
  const since = new Date(Date.now() - range * 3600_000).toISOString();

  let behavior: SourceBehavior | null = null;
  let status = 0;
  try {
    behavior = await api.sourceBehavior(id, { since, until });
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
  }

  if (!behavior) {
    return (
      <>
        <PageHeader
          guide={guideFor("/behavior/sources/[id]")}
          title={t("title")}
          meta={<Link href="/behavior/sources">{t("allSources")}</Link>}
        />
        <div className="notice" role="alert">
          {status === 404
            ? t("notFound")
            : status === 403
              ? errors("forbidden")
              : status === 401
                ? errors("expired")
                : t("failed")}
        </div>
      </>
    );
  }

  // Each of these enriches the page; none of them is the page. A failure in
  // one is reported in place rather than replacing everything with an error.
  const [metaResult, bucketsResult, anomaliesResult, activeResult, cellsResult] =
    await Promise.allSettled([
      api.logSource(id),
      api.sourceMetrics(id, { since, until, limit: METRIC_LIMIT }),
      // log_source_id also matches a WAF endpoint's log source, so the kind
      // must be pinned explicitly to keep this page on Phase A anomalies.
      api.anomalies({ log_source_id: id, limit: 20, since, until, subject_kind: "LOG_SOURCE" }),
      api.anomalies({
        log_source_id: id,
        active_only: true,
        limit: 200,
        subject_kind: "LOG_SOURCE",
      }),
      api.sourceBaselines(id),
    ]);

  const meta: LogSourceDetail | null =
    metaResult.status === "fulfilled" ? metaResult.value : null;
  const buckets: MetricBucket[] =
    bucketsResult.status === "fulfilled" ? bucketsResult.value : [];
  const anomalyPage: Page<AnomalySummary> | null =
    anomaliesResult.status === "fulfilled" ? anomaliesResult.value : null;
  const anomalies = anomalyPage?.items ?? [];
  const activePage: Page<AnomalySummary> | null =
    activeResult.status === "fulfilled" ? activeResult.value : null;
  const cells: BaselineCell[] =
    cellsResult.status === "fulfilled" ? cellsResult.value : [];

  const { active } = partitionAnomalies(activePage?.items ?? []);
  const { recent } = partitionAnomalies(anomalies);
  const quality = baselineQuality(behavior, cells, i18n);
  const metricsFailed = bucketsResult.status === "rejected";
  const health = collectionHealth(buckets, behavior.last_bucket_at, range * 3600, Date.now(), i18n);
  const unjudged = isUnjudged(behavior.state) || behavior.expected_eps == null;

  const points = buildSeries(buckets);
  const missing = buckets.filter((b) => b.completeness === "MISSING").length;
  const partial = buckets.filter((b) => b.completeness === "PARTIAL").length;

  const chartSummary = summarizeSourceTimeline(
    {
    hours: range,
    observed: points.map((p) => p.observed),
    currentEps: behavior.observed_eps,
    expectedEps: behavior.expected_eps,
    missing,
    partial,
    anomalyCount: anomalyPage?.total ?? null,
      shownAnomalyCount: anomalyPage?.items.length ?? null,
    },
    i18n,
  );

  // Secondary conditions worth stating next to the numbers they qualify.
  const notes: string[] = [];
  if (unjudged) notes.push(t("notes.learning"));
  if (quality.status === "DEGENERATE") notes.push(t("notes.zeroVariability"));
  // With the metric read failed there are no intervals to judge collection by:
  // saying "delayed" would turn a failed request into a claim about the source.
  if (!metricsFailed && health.status !== "CURRENT") {
    notes.push(
      t("notes.collection", {
        status: health.label.toLocaleLowerCase(locale === "tr" ? "tr-TR" : "en-US"),
      }),
    );
  }
  if (behavior.observed_eps === 0) notes.push(t("notes.silent"));
  if (meta?.monitoring_enabled === false) notes.push(t("notes.monitoringDisabled"));
  if (meta?.maintenance_mode) notes.push(t("notes.maintenance"));

  return (
    <>
      {/* --- 1. compact header ------------------------------------------- */}
      <SourceHeader behavior={behavior} meta={meta} />
      <p className="subtitle">{stateMeaningAgainst(
        behavior.state,
        {
          observed: behavior.observed_eps,
          low: behavior.expected_low,
          high: behavior.expected_high,
        },
        i18n,
      )}</p>
      <SourceEvaluationNotice source={behavior} />

      {/* --- 2. four primary metrics -------------------------------------- */}
      <div className="grid-4">
        <div className="card">
          <div className="k">{t("observedEps")}</div>
          {/* A measured zero is 0.00; only an absent measurement is an em
              dash. formatMetric enforces the distinction. An unjudged bucket
              still shows its collected EPS, qualified below, never a zero. */}
          <div className="v">{formatMetric(behavior.observed_eps, 2, locale)}</div>
          <p className="metric-note">
            {figureNewerThanVerdict(behavior)
              ? t.rich("collectedNewer", {
                  time: () => <UtcTime value={behavior.last_bucket_at} variant="compact" />,
                })
              : t("mostRecent")}
          </p>
        </div>
        <div className="card">
          <div className="k">{t("expectedEps")}</div>
          <div className="v">
            {/* No reliable baseline means no expectation exists. A 0 here
                would be an invented expectation the observed value is then
                judged against. */}
            {unjudged ? (
              <span className="metric-learning">{t("stillLearning")}</span>
            ) : (
              formatMetric(behavior.expected_eps, 2, locale)
            )}
          </div>
          <p className="metric-note">{t("expectedNote")}</p>
        </div>
        <div className="card">
          <div className="k">{t("deviation")}</div>
          {/* Not computed when there is no expectation to deviate from. */}
          <div className="v">
            {unjudged ? <span className="muted">—</span> : formatRatio(behavior.deviation_ratio, locale)}
          </div>
          <p className="metric-note">{t("deviationNote")}</p>
        </div>
        <div className="card">
          <div className="k">{t("lastEvent")}</div>
          <div className="v metric-timestamp">
            {behavior.last_event_at ? (
              <UtcTime value={behavior.last_event_at} />
            ) : (
              <span className="muted">—</span>
            )}
          </div>
          <p className="metric-note">
            {behavior.last_event_at ? t("lastEventNote") : t("noEvent")}
          </p>
        </div>
      </div>

      {notes.length > 0 && (
        <p className="confidence-line">
          <span className="muted">{notes.join(" · ")}</span>
        </p>
      )}

      {/* --- 3. the chart -------------------------------------------------- */}
      <section>
        <div className="row section-head">
          <h2 className="section-title">{t("volume")}</h2>
          <RangeSelector sourceId={behavior.log_source_id} active={range} />
        </div>
        {from7d && (
          <p className="notice" role="status">
            {i18n.t("behavior.source.note7d")}
          </p>
        )}
        {bucketsResult.status === "rejected" ? (
          <div className="notice" role="alert">
            {t("metricsFailed")}
          </div>
        ) : (
          <VolumeChart
            buckets={buckets}
            expected={unjudged ? null : behavior.expected_eps}
            expectedLow={behavior.expected_low}
            expectedHigh={behavior.expected_high}
            anomalies={anomalies
              .filter((a) => a.anomaly_start)
              .map((a) => ({
                start: a.anomaly_start as string,
                end: a.anomaly_end,
                label: detectorLabel(a.anomaly_type, i18n),
              }))}
            ariaLabel={t("chartAria", { name: behavior.name })}
            textSummary={chartSummary}
          />
        )}
      </section>

      {/* --- 4. anomalies, above the technical detail ---------------------- */}
      <section>
        <h2 className="section-title">{t("anomalies")}</h2>
        <SourceAnomalies
          active={active}
          recent={recent}
          activeFailed={activeResult.status === "rejected"}
          recentFailed={anomaliesResult.status === "rejected"}
          windowLabel={rangeLabel(range, i18n)}
          unjudged={unjudged}
        />
      </section>

      {/* --- 5 & 6. baseline quality and collection health ----------------- */}
      <section>
        <h2 className="section-title">{t("quality")}</h2>
        <SourceQuality
          baseline={quality}
          collection={health}
          collectionUnknown={metricsFailed}
          expectedLow={behavior.expected_low}
          expectedHigh={behavior.expected_high}
          lastBucketAt={behavior.last_bucket_at}
          qradarStatus={meta?.qradar_status ?? null}
        />
      </section>

      {/* --- 7. advanced, all collapsed ------------------------------------ */}
      <section>
        <h2 className="section-title">{t("technical")}</h2>
        <SourceAdvanced
          behavior={behavior}
          meta={meta}
          buckets={buckets}
          cells={cells}
          cellsFailed={cellsResult.status === "rejected"}
          bucketsFailed={metricsFailed}
        />
      </section>
    </>
  );
}
