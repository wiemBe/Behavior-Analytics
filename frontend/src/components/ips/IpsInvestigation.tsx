// The IPS investigation view: what changed on this entity or IPS source, against
// what normal.
//
// Composition only. The page fetches; this arranges. Order follows the WAF view
// (guidelines §3): who and what, the deterministic summary, the measurements,
// the series, what was judged, the evidence statement, the lifecycle. An IPS
// incident has no evidence package and no contributor list, so there is no
// "what changed" section: the evidence section says so in one line instead of
// rendering an empty table.

import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import { stateLabel, statePillText } from "@/components/waf/wafLabels";
import type {
  IpsAnomalyDetail,
  IpsBaselineCell,
  IpsEntityMetricBucket,
  IpsSourceMetricBucket,
} from "@/lib/api";

import { reliableCellCount, type IpsBaselineSource } from "./ipsBaseline";
import { detectorSpec, ipsFigures } from "./ipsDetectors";
import { bucketWidth } from "./ipsFormat";
import { ipsStateMeaning, ipsSubjectText, ipsTypeLabel } from "./ipsLabels";
import {
  buildIpsEntitySeries,
  buildIpsSourceSeries,
  ipsSeriesCounts,
  SOURCE_SERIES,
  type IpsPoint,
} from "./ipsSeries";
import { IpsDetectionBlock } from "./IpsDetectionBlock";
import { IpsIncidentHeader } from "./IpsIncidentHeader";
import { IpsIncidentMetrics } from "./IpsIncidentMetrics";
import { IpsSeriesChart } from "./IpsSeriesChart";
import { summarizeIpsAnomaly } from "./ipsSummary";

import "./ips.css";

/** The stored buckets around the incident: the entity's, or the source's. */
export type IpsSeriesData =
  | { kind: "entity"; buckets: IpsEntityMetricBucket[] }
  | { kind: "source"; buckets: IpsSourceMetricBucket[] };

interface Plan {
  points: IpsPoint[];
  rows: Array<{ bucket_start: string; bucket_seconds: number }>;
}

/**
 * The series this incident's detector judges, or the reason there is none.
 * Decided by the subject kind and the detector together, so a pairing the API
 * should not produce is stated rather than drawn from the wrong data.
 */
function plan(
  anomaly: IpsAnomalyDetail,
  series: IpsSeriesData,
): { plan: Plan } | { why: string } {
  const type = anomaly.anomaly_type;
  const spec = detectorSpec(type);
  if (!spec) {
    return {
      why: `This build has no series for the detector ${type}, so none is drawn rather than a guess.`,
    };
  }
  if (anomaly.subject_kind === "IPS_ENTITY") {
    if (!anomaly.ips_entity) {
      return { why: "This incident names no entity, so there is no entity series to read." };
    }
    if (spec.scope !== "entity" || series.kind !== "entity") {
      return { why: `The detector ${ipsTypeLabel(type)} does not judge an entity series.` };
    }
    return { plan: { points: buildIpsEntitySeries(series.buckets), rows: series.buckets } };
  }
  const kind = SOURCE_SERIES[type];
  if (!kind || series.kind !== "source") {
    return { why: `The detector ${ipsTypeLabel(type)} does not judge a source-level series.` };
  }
  return { plan: { points: buildIpsSourceSeries(series.buckets, kind), rows: series.buckets } };
}

/** The chart as prose: what a reader of the canvas would see. */
export function ipsChartSummary(
  anomaly: IpsAnomalyDetail,
  points: IpsPoint[],
  quantity: string,
): string {
  const c = ipsSeriesCounts(points);
  const d = anomaly.detection;
  const parts = [`${quantity} for ${ipsSubjectText(anomaly)} across ${c.stored} stored buckets.`];
  if (d?.expected_low != null && d.expected_high != null) {
    const f = ipsFigures(anomaly, d);
    const scale = f.unit === "share" ? 100 : 1;
    const unit = f.unit === "share" ? "%" : f.unit === "ports" ? " distinct ports" : " events per bucket";
    parts.push(
      `The expected range at detection was ${(d.expected_low * scale).toFixed(1)} to ${(d.expected_high * scale).toFixed(1)}${unit}.`,
    );
  }
  if (c.gaps > 0) parts.push(`${c.gaps} intervals had no stored bucket and are drawn as gaps.`);
  if (c.notMeasured > 0) {
    parts.push(`${c.notMeasured} stored buckets were not measured and are drawn as gaps, not as zero.`);
  }
  if (c.lowerBound > 0) parts.push(`${c.lowerBound} buckets are lower bounds.`);
  if (c.maintenance > 0) parts.push(`${c.maintenance} buckets were in maintenance.`);
  return parts.join(" ");
}

function Banner({ word, children }: { word: string; children: React.ReactNode }) {
  return (
    <p className="banner warn">
      <span>
        <strong>{word}.</strong> {children}
      </span>
    </p>
  );
}

export function IpsInvestigation({
  anomaly,
  series,
  baselines = null,
  canOpenSource = false,
}: {
  anomaly: IpsAnomalyDetail;
  /** Stored buckets around the incident, or null when they could not be loaded. */
  series: IpsSeriesData | null;
  /** The subject's seasonal cells, or null when they could not be loaded. */
  baselines?: IpsBaselineCell[] | null;
  /** Whether the caller may read the IPS source's administration page. */
  canOpenSource?: boolean;
}) {
  const spec = detectorSpec(anomaly.anomaly_type);
  const d = anomaly.detection;
  const f = ipsFigures(anomaly, d);
  const subject = anomaly.subject_kind === "IPS_ENTITY" ? "entity" : "source";

  const planned = series ? plan(anomaly, series) : null;
  const points = planned && "plan" in planned ? planned.plan.points : null;
  const bucketSeconds = series?.buckets[0]?.bucket_seconds ?? null;
  const counts = points ? ipsSeriesCounts(points) : null;
  const summary = summarizeIpsAnomaly(anomaly, bucketSeconds);

  // Only a detector judged against a seasonal cell has a seasonal band.
  const metric = spec?.baselineMetric ?? null;
  const baseline: IpsBaselineSource | null =
    baselines && metric ? { cells: baselines, metric, pooled: f.unit === "share" } : null;
  const noReliableCell = baseline != null && reliableCellCount(baseline.cells, baseline.metric) === 0;
  // A reliable-cell-free source draws no band at all; the chart then has nothing to
  // follow, so it falls back to the recorded range rather than a flat zero.
  const chartBaseline = noReliableCell ? null : baseline;

  const expected = f.expected;
  const quantity = spec?.quantity ?? "";

  return (
    <>
      <IpsIncidentHeader anomaly={anomaly} canOpenSource={canOpenSource} />

      <p className="incident-summary">{summary.text}</p>
      {summary.caveat && <p className="subtitle">{summary.caveat}</p>}
      <p className="subtitle">{ipsStateMeaning(anomaly.state, subject)}</p>

      <IpsIncidentMetrics anomaly={anomaly} bucketSeconds={bucketSeconds} />

      <section>
        <h2 className="section-title">Timeline</h2>
        {series == null ? (
          <div className="notice" role="alert">
            The measurement history for this {subject === "entity" ? "entity" : "IPS source"} could
            not be loaded. The rest of this investigation is unaffected, but the series is absent
            rather than empty. Reload the page; if it keeps failing, check that the backend is
            reachable.
          </div>
        ) : planned && "why" in planned ? (
          <div className="notice">{planned.why}</div>
        ) : points && points.length === 0 ? (
          <div className="notice">
            No buckets were stored for this {subject === "entity" ? "entity" : "IPS source"} in
            this window. This is an absence of collection, not an observation of zero{" "}
            {f.unit === "share" ? "share" : f.unit === "ports" ? "ports" : "events"}.
          </div>
        ) : (
          points &&
          planned &&
          "plan" in planned &&
          f.unit != null && (
            <>
              {counts && counts.notExact > 0 && (
                <Banner word="PARTIAL">
                  {counts.notExact} of {counts.stored} stored buckets were not measured exactly
                  {counts.notMeasured > 0 &&
                    `; ${counts.notMeasured} of them carry no usable value and are drawn as gaps`}
                  {counts.lowerBound > 0 && `; ${counts.lowerBound} are plotted as lower bounds`}.
                  A figure from those buckets does not support a claim of exact volume or exact
                  normality.
                </Banner>
              )}
              {counts && counts.admissionTruncated > 0 && (
                <Banner word="TRUNCATED">
                  Entity admission lost entities to the quota, ceiling or top-N cut in{" "}
                  {counts.admissionTruncated} of {counts.stored} buckets, so the inventory for those
                  buckets is incomplete. This entity&apos;s own counts are shown; nothing is implied about
                  entities that were not admitted.
                </Banner>
              )}
              {counts && counts.maintenance > 0 && (
                <p className="subtitle">
                  {counts.maintenance} bucket{counts.maintenance === 1 ? " was" : "s were"} in
                  maintenance and {counts.maintenance === 1 ? "is" : "are"} outlined on the chart: the
                  engine does not judge them.
                </p>
              )}
              {baselines == null && metric && (
                <Banner word="BASELINE UNAVAILABLE">
                  The seasonal cells could not be loaded, so the band is the range recorded at
                  detection, not a per-bucket one.
                </Banner>
              )}
              {noReliableCell && (
                <Banner word="INSUFFICIENT_DATA">
                  No seasonal cell of this metric is reliable yet, so no expected band is drawn
                  for any bucket. The recorded range at detection, if any, is shown instead. That
                  is not the same as a normal baseline.
                </Banner>
              )}
              <IpsSeriesChart
                points={planned.plan.points}
                rows={planned.plan.rows}
                unit={f.unit === "since" ? "events" : f.unit}
                quantity={quantity}
                bucketSeconds={bucketSeconds}
                baseline={chartBaseline}
                expected={expected}
                expectedLow={d?.expected_low ?? null}
                expectedHigh={d?.expected_high ?? null}
                anomalies={
                  anomaly.anomaly_start
                    ? [{ start: anomaly.anomaly_start, end: anomaly.anomaly_end }]
                    : []
                }
                transitions={anomaly.transitions.map((t) => ({
                  at: t.occurred_at,
                  label: stateLabel(t.to_state),
                }))}
                ariaLabel={`${quantity} against the expected range`}
                textSummary={ipsChartSummary(anomaly, planned.plan.points, quantity)}
              />
            </>
          )
        )}
      </section>

      <section>
        <h2 className="section-title">What was judged</h2>
        <IpsDetectionBlock anomaly={anomaly} bucketSeconds={bucketSeconds} />
      </section>

      <section>
        <h2 className="section-title">Evidence</h2>
        <p className="subtitle">
          An IPS incident has no evidence package and no list of contributing values. The series
          above and what was judged are what the detector recorded; source addresses are not
          served for IPS.
        </p>
      </section>

      <section>
        <h2 className="section-title">Lifecycle</h2>
        <LifecycleTimeline
          transitions={anomaly.transitions}
          policyVersion={anomaly.policy_version}
          stateLabel={statePillText}
        />
      </section>
    </>
  );
}
