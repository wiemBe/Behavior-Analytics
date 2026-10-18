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
import Link from "next/link";

import { EvidenceBanner } from "@/components/behavior/EvidenceBanner";
import { DimensionExplorer } from "@/components/behavior/DimensionExplorer";
import { IncidentHeader } from "@/components/behavior/IncidentHeader";
import { IncidentMetrics } from "@/components/behavior/IncidentMetrics";
import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import { TechnicalDetails } from "@/components/behavior/TechnicalDetails";
import { TopContributors } from "@/components/behavior/TopContributors";
import { VolumeChart } from "@/components/behavior/VolumeChart";
import { AnomalyTabs } from "@/components/behavior/AnomalyTabs";
import { detectorSpec } from "@/components/ips/ipsDetectors";
import { IpsInvestigation, type IpsSeriesData } from "@/components/ips/IpsInvestigation";
import { PwInvestigation, type PwInvestigationData } from "@/components/propertywatch/PwInvestigation";
import { PageHeader } from "@/components/ui/PageHeader";
import { WafInvestigation } from "@/components/waf/WafInvestigation";
import {
  ApiError,
  api,
  isIpsDetail,
  isPwDetail,
  isWafDetail,
  type AnomalyDetail,
  type IpsAnomalyDetail,
  type IpsBaselineCell,
  type MetricBucket,
  type PwAnomalyDetail,
  type PwValueSeries,
  type WafAnomalyDetail,
  type WafBaselineCell,
  type WafMetricBucket,
} from "@/lib/api";
import { ratioSpecFor } from "@/components/waf/wafSeries";
import { dimensionLabel, hasConfidenceLimitation, stateMeaning } from "@/lib/behavior";
import { summarizeAnomaly, summarizeTimeline } from "@/lib/summarize";
import { buildSeries } from "@/lib/timeseries";

import "./incident.css";

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

/** The window an IPS series is read for: the incident with context either side. */
function ipsWindow(anomaly: IpsAnomalyDetail): { since: string; until: string } {
  const anchor = Date.parse(anomaly.anomaly_start ?? anomaly.detected_at);
  const end = Date.parse(anomaly.anomaly_end ?? anomaly.resolved_at ?? anomaly.detected_at);
  return {
    since: new Date(anchor - CONTEXT_HOURS * 3600_000).toISOString(),
    until: new Date(Math.max(end, anchor) + CONTEXT_HOURS * 3600_000).toISOString(),
  };
}

/**
 * The stored buckets around an IPS incident, or null if they could not be loaded:
 * the entity's own for an entity incident, the source's for a source incident.
 * Null is not an empty list: the view says "could not be loaded", not "nothing
 * was collected".
 */
async function ipsSeries(anomaly: IpsAnomalyDetail): Promise<IpsSeriesData | null> {
  const window = { ...ipsWindow(anomaly), limit: 2000 };
  try {
    if (anomaly.subject_kind === "IPS_ENTITY") {
      if (!anomaly.ips_entity) return { kind: "entity", buckets: [] };
      const page = await api.ipsEntityMetrics(anomaly.ips_entity.id, window);
      return { kind: "entity", buckets: page.items };
    }
    if (anomaly.log_source_id === null) return null;
    const page = await api.ipsSourceMetrics(anomaly.log_source_id, window);
    return { kind: "source", buckets: page.items };
  } catch {
    return null;
  }
}

/**
 * The seasonal cells of the metric this detector reads, or null if they could
 * not be loaded (or the detector has no seasonal metric). Null is not "no cells":
 * the view then says it fell back to the range recorded at detection.
 */
async function ipsBaselines(anomaly: IpsAnomalyDetail): Promise<IpsBaselineCell[] | null> {
  const metric = detectorSpec(anomaly.anomaly_type)?.baselineMetric;
  if (!metric) return null;
  const subject = anomaly.subject_kind === "IPS_ENTITY" ? anomaly.ips_entity?.id : anomaly.log_source_id;
  if (!subject) return null;
  const cells: IpsBaselineCell[] = [];
  try {
    // One metric holds at most 168 cells; a few pages are a bound, not a loop over entities.
    for (let offset = 0; offset < 1000; offset += 200) {
      const params = { metric, limit: 200, offset };
      const page =
        anomaly.subject_kind === "IPS_ENTITY"
          ? await api.ipsEntityBaselines(subject, params)
          : await api.ipsSourceBaselines(subject, params);
      cells.push(...page.items);
      if (cells.length >= page.total || page.items.length === 0) break;
    }
    return cells;
  } catch {
    return null;
  }
}

/**
 * Whether the caller may open the IPS source's administration page (admin:ips).
 * A failed read of the caller's grants is "no": a link is withheld, never guessed.
 */
async function canOpenIpsSource(): Promise<boolean> {
  try {
    const me = await api.caller();
    return me.effective_permissions.includes("admin:ips") || me.permissions.includes("admin:*");
  } catch {
    return false;
  }
}

/** The series read serves at most this many hours back from now. */
const PW_SERIES_MAX_HOURS = 168;

/**
 * What the property-watch view draws: the value's id (looked up by its text among the watch's
 * tracked values, never put in a URL), its series around the incident, and the watch's buckets.
 * Every part is optional: a failed read is `null` and the view says so, it is not "nothing".
 */
async function pwData(anomaly: PwAnomalyDetail): Promise<PwInvestigationData> {
  const anchor = Date.parse(anomaly.anomaly_start ?? anomaly.detected_at);
  const end = Date.parse(anomaly.anomaly_end ?? anomaly.resolved_at ?? anomaly.detected_at);
  const now = Date.now();
  const windowStart = new Date(anchor - CONTEXT_HOURS * 3600_000);
  const windowEnd = new Date(Math.min(now, Math.max(end, anchor) + CONTEXT_HOURS * 3600_000));
  const base = { windowStart: windowStart.toISOString(), windowEnd: windowEnd.toISOString() };
  const hours = Math.ceil((now - windowStart.getTime()) / 3600_000);
  const watchId = anomaly.pw_value.watch_id;

  let valueId: string | null = null;
  try {
    const page = await api.propertyWatchValues(watchId, { hours: PW_SERIES_MAX_HOURS, limit: 200 });
    valueId = page.items.find((v) => v.value_key === anomaly.pw_value.value_key)?.id ?? null;
  } catch {
    valueId = null;
  }
  if (hours > PW_SERIES_MAX_HOURS) {
    return {
      ...base,
      valueId,
      series: null,
      buckets: null,
      chartNote: `This incident is older than the ${PW_SERIES_MAX_HOURS / 24} days the series read covers, so its chart is not drawn. The incident's own figures and lifecycle are shown.`,
    };
  }
  if (valueId === null) {
    return {
      ...base,
      valueId,
      series: null,
      buckets: null,
      chartNote:
        "This value could not be found among the watch's tracked values, so its series is not drawn. It may have been retired, or the tracked values could not be read.",
    };
  }
  const [series, buckets] = await Promise.all([
    api
      .propertyWatchSeries(watchId, [valueId], hours)
      .then((r) => r.series[0] ?? null)
      .catch(() => null as PwValueSeries | null),
    api.propertyWatchBuckets(watchId, hours).catch(() => null),
  ]);
  return {
    ...base,
    valueId,
    series,
    buckets,
    chartNote:
      series === null
        ? "The series of this value could not be loaded. The rest of this investigation is unaffected, but the chart is absent rather than empty."
        : null,
  };
}

export default async function AnomalyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let loaded: AnomalyDetail | WafAnomalyDetail | IpsAnomalyDetail | PwAnomalyDetail | null = null;
  let status = 0;
  try {
    loaded = await api.anomaly(id);
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
  }

  if (!loaded) {
    return (
      <>
        <PageHeader title="Anomaly investigation" />
        <div className="notice" role="alert">
          <p>
            {status === 404
              ? "This anomaly does not exist. It may have been removed, or the link may be wrong."
              : status === 403
                ? "You do not have permission to view anomaly evidence. Ask an administrator for the read:anomalies grant."
                : status === 401
                  ? "Your session has expired. Sign in again to continue."
                  : "Could not load this anomaly. The backend may be unreachable; reload the page to try again."}
          </p>
          <p className="incident-error-link">
            <Link href="/anomalies">Back to all anomalies</Link>
          </p>
        </div>
      </>
    );
  }

  // A property-watch value is investigated against its own watch's series, in events per bucket.
  if (isPwDetail(loaded)) {
    return <PwInvestigation anomaly={loaded} data={await pwData(loaded)} />;
  }
  // A WAF endpoint is investigated against its own request buckets, in requests
  // rather than EPS, so it has its own view; the Phase A page below is unchanged.
  if (isWafDetail(loaded)) {
    const [buckets, baselines] = await Promise.all([wafBuckets(loaded), wafBaselines(loaded)]);
    return <WafInvestigation anomaly={loaded} buckets={buckets} baselines={baselines} />;
  }
  // An IPS entity or source is investigated against its own event, port or share
  // series, in that unit and never in EPS, so it has its own view as well.
  if (isIpsDetail(loaded)) {
    const [series, baselines, canOpenSource] = await Promise.all([
      ipsSeries(loaded),
      ipsBaselines(loaded),
      canOpenIpsSource(),
    ]);
    return (
      <IpsInvestigation
        anomaly={loaded}
        series={series}
        baselines={baselines}
        canOpenSource={canOpenSource}
      />
    );
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
    if (anomaly.log_source_id === null) throw new Error("no source");
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
          evidence={(
            <DimensionExplorer
              dimensions={pkg?.dimensions ?? []}
              evidenceStatus={anomaly.evidence_status}
            />
          )}
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
