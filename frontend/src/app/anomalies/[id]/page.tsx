// Anomaly investigation — the product's flagship page.
//
// It answers one question: what changed, when, and what evidence supports it?
// Everything on it is server-rendered from the backend's own verdict. The UI
// never recomputes detection logic and never fills a missing measurement with
// a plausible number.
//
// The information order is deliberate and is the whole redesign. Previously
// the first viewport held eight equally weighted metric cards — including the
// robust z-score — then a versions table, then a fourteen-row threshold table,
// and the timeline appeared below all of it. Ten contributor tables and a
// twenty-row provenance dump with expanded AQL followed: 26 tables and 125
// column headers in one scroll.
//
// Now: who and what, the deterministic summary, four metrics, the timeline,
// the three strongest contributors, and everything else behind three tabs.

import type { Metadata } from "next";

import { EvidenceBanner } from "@/components/behavior/EvidenceBanner";
import { DimensionExplorer } from "@/components/behavior/DimensionExplorer";
import { IncidentHeader } from "@/components/behavior/IncidentHeader";
import { IncidentMetrics } from "@/components/behavior/IncidentMetrics";
import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import { TechnicalDetails } from "@/components/behavior/TechnicalDetails";
import { TopContributors } from "@/components/behavior/TopContributors";
import { VolumeChart } from "@/components/behavior/VolumeChart";
import { AnomalyTabs } from "@/components/behavior/AnomalyTabs";
import { WafInvestigation } from "@/components/waf/WafInvestigation";
import {
  ApiError,
  api,
  isWafDetail,
  type AnomalyDetail,
  type MetricBucket,
  type WafAnomalyDetail,
  type WafBaselineCell,
  type WafMetricBucket,
} from "@/lib/api";
import { ratioSpecFor } from "@/components/waf/wafSeries";
import { dimensionLabel, hasConfidenceLimitation, stateMeaning } from "@/lib/behavior";
import { summarizeAnomaly, summarizeTimeline } from "@/lib/summarize";
import { buildSeries } from "@/lib/timeseries";

export const metadata: Metadata = {
  title: "Anomaly investigation",
};

/** How much surrounding context the timeline shows around the anomaly. */
const CONTEXT_HOURS = 6;

/**
 * The stored buckets around a WAF incident, or null if they could not be loaded.
 * A failed fetch must not take down the investigation, and null is not an empty
 * list: the page says "could not be loaded" rather than "nothing was collected".
 */
async function wafBuckets(anomaly: WafAnomalyDetail): Promise<WafMetricBucket[] | null> {
  const anchor = Date.parse(anomaly.anomaly_start ?? anomaly.detected_at);
  const end = Date.parse(anomaly.anomaly_end ?? anomaly.resolved_at ?? anomaly.detected_at);
  try {
    const page = await api.wafEndpointMetrics(anomaly.waf_endpoint.id, {
      since: new Date(anchor - CONTEXT_HOURS * 3600_000).toISOString(),
      until: new Date(Math.max(end, anchor) + CONTEXT_HOURS * 3600_000).toISOString(),
      limit: 2000,
    });
    return page.items;
  } catch {
    return null;
  }
}

/**
 * The seasonal cells of the metric this detector reads, or null if they could
 * not be loaded. Null is not "no cells": the chart then says it fell back to the
 * range recorded at detection, rather than drawing no band as if none existed.
 */
async function wafBaselines(anomaly: WafAnomalyDetail): Promise<WafBaselineCell[] | null> {
  const metric = ratioSpecFor(anomaly.anomaly_type)?.baselineMetric ?? "request_count";
  const cells: WafBaselineCell[] = [];
  try {
    // One metric holds at most 168 cells per normalizer version; a few pages
    // are a bound, not a loop over endpoints.
    for (let offset = 0; offset < 1000; offset += 200) {
      const page = await api.wafEndpointBaselines(anomaly.waf_endpoint.id, {
        metric,
        limit: 200,
        offset,
      });
      cells.push(...page.items);
      if (cells.length >= page.total || page.items.length === 0) break;
    }
    return cells;
  } catch {
    return null;
  }
}

export default async function AnomalyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let loaded: AnomalyDetail | WafAnomalyDetail | null = null;
  let status = 0;
  try {
    loaded = await api.anomaly(id);
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
  }

  if (!loaded) {
    return (
      <>
        <h1 className="page-title">Anomaly investigation</h1>
        <div className="notice" role="alert">
          {status === 404
            ? "This anomaly does not exist."
            : status === 403
              ? "You do not have permission to view anomaly evidence."
              : status === 401
                ? "Your session has expired. Sign in again to continue."
                : "Could not load this anomaly. The backend may be unreachable."}
        </div>
      </>
    );
  }

  // A WAF endpoint is investigated against its own request buckets, in requests
  // rather than EPS, so it has its own view; the Phase A page below is unchanged.
  if (isWafDetail(loaded)) {
    const [buckets, baselines] = await Promise.all([wafBuckets(loaded), wafBaselines(loaded)]);
    return <WafInvestigation anomaly={loaded} buckets={buckets} baselines={baselines} />;
  }
  const anomaly: AnomalyDetail = loaded;

  // The timeline is context around the incident, so the window is anchored to
  // the anomaly rather than to now — an anomaly from last week must still
  // render its own interval.
  const anchor = Date.parse(anomaly.anomaly_start ?? anomaly.detected_at);
  const since = new Date(anchor - CONTEXT_HOURS * 3600_000).toISOString();
  const until = new Date(
    Math.max(
      Date.parse(anomaly.anomaly_end ?? anomaly.resolved_at ?? anomaly.detected_at),
      anchor,
    ) +
      CONTEXT_HOURS * 3600_000,
  ).toISOString();

  // A failed metric fetch must not take down the whole investigation: the
  // summary, the metrics, the evidence and the lifecycle are the substance.
  let buckets: MetricBucket[] = [];
  let bucketsFailed = false;
  try {
    buckets = await api.sourceMetrics(anomaly.log_source_id, { since, until, limit: 2000 });
  } catch {
    bucketsFailed = true;
  }

  const pkg = anomaly.explanation_package;
  const detection = anomaly.detection;
  const summary = summarizeAnomaly(anomaly);
  const degenerate = hasConfidenceLimitation(detection?.robust_score_status);

  const points = buildSeries(buckets);
  const incomplete = buckets.filter((b) => b.completeness !== "COMPLETE").length;
  const chartSummary = summarizeTimeline({
    observed: points.map((p) => p.observed),
    expected: detection?.expected_eps ?? anomaly.expected_value,
    anomalyStart: anomaly.anomaly_start,
    anomalyEnd: anomaly.anomaly_end,
    state: anomaly.state,
    incompleteBuckets: incomplete,
    totalBuckets: buckets.length,
  });

  const truncated = (pkg?.dimensions ?? [])
    .filter((d) => d.availability === "TRUNCATED")
    .map((d) => dimensionLabel(d.dimension));

  return (
    <>
      {/* --- A. compact incident header ---------------------------------- */}
      <IncidentHeader anomaly={anomaly} />

      {/* --- B. deterministic summary ------------------------------------ */}
      <p className="incident-summary">{summary.text}</p>
      {summary.caveat && <p className="subtitle">{summary.caveat}</p>}
      <p className="subtitle">{stateMeaning(anomaly.state)}</p>

      {/* --- C. the four primary metrics, as one comparison --------------- */}
      <IncidentMetrics anomaly={anomaly} confidenceLimited={degenerate} />

      {/* --- D. the timeline --------------------------------------------- */}
      <section>
        <h2 className="section-title">Timeline</h2>
        {bucketsFailed ? (
          <div className="notice" role="alert">
            Metric history could not be loaded. The rest of this investigation
            is unaffected, but the timeline is absent rather than empty.
          </div>
        ) : (
          <VolumeChart
            buckets={buckets}
            expected={detection?.expected_eps ?? anomaly.expected_value}
            expectedLow={detection?.expected_low ?? null}
            expectedHigh={detection?.expected_high ?? null}
            anomalies={
              anomaly.anomaly_start
                ? [{ start: anomaly.anomaly_start, end: anomaly.anomaly_end }]
                : []
            }
            transitions={anomaly.transitions.map((t) => ({
              at: t.occurred_at,
              label: t.to_state,
            }))}
            ariaLabel="Observed volume, expected baseline and the anomalous interval"
            textSummary={chartSummary}
          />
        )}
      </section>

      {/* --- E. what changed --------------------------------------------- */}
      <section>
        <h2 className="section-title">What changed</h2>
        <p className="subtitle">
          The strongest contributors, one per dimension. These are the values
          that moved most during the interval — a measured share of the change,
          not a statement of cause.
        </p>
        <TopContributors
          packaged={pkg}
          anomalyType={anomaly.anomaly_type}
          evidenceStatus={anomaly.evidence_status}
        />
      </section>

      {/* --- F. evidence completeness, stated once ------------------------ */}
      <EvidenceBanner
        status={anomaly.evidence_status}
        error={pkg?.error ?? null}
        truncatedDimensions={truncated}
      />

      {/* --- G. everything else, behind tabs ------------------------------ */}
      <section id="evidence">
        <h2 className="section-title">Investigation detail</h2>
        <AnomalyTabs
          evidence={<DimensionExplorer dimensions={pkg?.dimensions ?? []} />}
          lifecycle={
            <LifecycleTimeline
              transitions={anomaly.transitions}
              policyVersion={anomaly.policy_version}
            />
          }
          technical={<TechnicalDetails anomaly={anomaly} packaged={pkg} />}
        />
      </section>
    </>
  );
}
