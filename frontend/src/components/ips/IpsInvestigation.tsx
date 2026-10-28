// The IPS investigation view: what changed on this entity or IPS source, against
// what normal.
//
// Composition only. The page fetches; this arranges. Order follows the WAF view
// (guidelines §3): who and what, the deterministic summary, the measurements,
// the series, what was judged, the evidence statement, the lifecycle. An IPS
// incident has no evidence package and no contributor list, so there is no
// "what changed" section: the evidence section says so in one line instead of
// rendering an empty table.

import { useTranslations } from "next-intl";

import { LifecycleTimeline } from "@/components/behavior/LifecycleTimeline";
import type {
  IpsAnomalyDetail,
  IpsBaselineCell,
  IpsEntityMetricBucket,
  IpsSourceMetricBucket,
} from "@/lib/api";
import { wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { formatShare } from "@/lib/format";
import { fixed, grouped } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { reliableCellCount, type IpsBaselineSource } from "./ipsBaseline";
import { detectorSpec, ipsFigures, quantityText } from "./ipsDetectors";
import { ENGLISH_IPS } from "./ipsI18n";
import { ipsStateMeaning, ipsStateWord, ipsSubjectText, ipsTypeLabel } from "./ipsLabels";
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
import type { OriginRow } from "@/components/behavior/historyOrigin";
import { IpsSeriesChart } from "./IpsSeriesChart";
import { summarizeIpsAnomaly } from "./ipsSummary";

import "./ips.css";

/** The stored buckets around the incident: the entity's, or the source's. */
export type IpsSeriesData =
  | { kind: "entity"; buckets: IpsEntityMetricBucket[] }
  | { kind: "source"; buckets: IpsSourceMetricBucket[] };

interface Plan {
  points: IpsPoint[];
  rows: OriginRow[];
}

/**
 * The series this incident's detector judges, or the reason there is none.
 * Decided by the subject kind and the detector together, so a pairing the API
 * should not produce is stated rather than drawn from the wrong data.
 */
function plan(anomaly: IpsAnomalyDetail, series: IpsSeriesData, i18n: BehaviorI18n): { plan: Plan } | { why: string } {
  const type = anomaly.anomaly_type;
  const spec = detectorSpec(type);
  if (!spec) {
    return {
      why: i18n.t("ips.plan.noSeries", { type }),
    };
  }
  if (anomaly.subject_kind === "IPS_ENTITY") {
    if (!anomaly.ips_entity) {
      return { why: i18n.t("ips.plan.noEntity") };
    }
    if (spec.scope !== "entity" || series.kind !== "entity") {
      return { why: i18n.t("ips.plan.notEntity", { name: ipsTypeLabel(type, i18n) }) };
    }
    return { plan: { points: buildIpsEntitySeries(series.buckets), rows: series.buckets } };
  }
  const kind = SOURCE_SERIES[type];
  if (!kind || series.kind !== "source") {
    return { why: i18n.t("ips.plan.notSource", { name: ipsTypeLabel(type, i18n) }) };
  }
  return { plan: { points: buildIpsSourceSeries(series.buckets, kind), rows: series.buckets } };
}

/** The chart as prose: what a reader of the canvas would see. */
export function ipsChartSummary(
  anomaly: IpsAnomalyDetail,
  points: IpsPoint[],
  quantity: string,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  const c = ipsSeriesCounts(points);
  const d = anomaly.detection;
  const n = (v: number) => grouped(v, i18n.locale);
  const t = i18n.t;
  const parts = [
    t("ips.chartSummary.lead", {
      quantity,
      subject: ipsSubjectText(anomaly, i18n),
      count: n(c.stored),
    }),
  ];
  if (d?.expected_low != null && d.expected_high != null) {
    const f = ipsFigures(anomaly, d);
    const range =
      f.unit === "share"
        ? t("ips.chartSummary.rangeShare", {
            low: formatShare(d.expected_low, i18n.locale),
            high: formatShare(d.expected_high, i18n.locale),
          })
        : t(f.unit === "ports" ? "ips.chartSummary.rangePorts" : "ips.chartSummary.rangeEvents", {
            low: fixed(d.expected_low, 1, i18n.locale),
            high: fixed(d.expected_high, 1, i18n.locale),
          });
    parts.push(t("ips.chartSummary.rangeAt", { range }));
  }
  if (c.gaps > 0) parts.push(t("ips.chartSummary.gaps", { count: n(c.gaps) }));
  if (c.notMeasured > 0) {
    parts.push(t("ips.chartSummary.notMeasured", { count: n(c.notMeasured) }));
  }
  if (c.lowerBound > 0) parts.push(t("ips.chartSummary.lowerBounds", { count: n(c.lowerBound) }));
  if (c.maintenance > 0) parts.push(t("ips.chartSummary.maintenance", { count: n(c.maintenance) }));
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
  const t = useTranslations("ips");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const spec = detectorSpec(anomaly.anomaly_type);
  const d = anomaly.detection;
  const f = ipsFigures(anomaly, d);
  const subject = anomaly.subject_kind === "IPS_ENTITY" ? "entity" : "source";
  const subjectNoun = t(subject === "entity" ? "subject.entityNoun" : "subject.ipsSourceNoun");

  const planned = series ? plan(anomaly, series, i18n) : null;
  const points = planned && "plan" in planned ? planned.plan.points : null;
  const bucketSeconds = series?.buckets[0]?.bucket_seconds ?? null;
  const counts = points ? ipsSeriesCounts(points) : null;
  const summary = summarizeIpsAnomaly(anomaly, bucketSeconds, i18n);

  // Only a detector judged against a seasonal cell has a seasonal band.
  const metric = spec?.baselineMetric ?? null;
  const baseline: IpsBaselineSource | null =
    baselines && metric ? { cells: baselines, metric, pooled: f.unit === "share" } : null;
  const noReliableCell = baseline != null && reliableCellCount(baseline.cells, baseline.metric) === 0;
  // A reliable-cell-free source draws no band at all; the chart then has nothing to
  // follow, so it falls back to the recorded range rather than a flat zero.
  const chartBaseline = noReliableCell ? null : baseline;

  const expected = f.expected;
  const quantity = quantityText(anomaly.anomaly_type, i18n);

  return (
    <>
      <IpsIncidentHeader anomaly={anomaly} canOpenSource={canOpenSource} />

      <p className="incident-summary">{summary.text}</p>
      {summary.caveat && <p className="subtitle">{summary.caveat}</p>}
      <p className="subtitle">{ipsStateMeaning(anomaly.state, subject, i18n)}</p>

      <IpsIncidentMetrics anomaly={anomaly} bucketSeconds={bucketSeconds} />

      <section>
        <h2 className="section-title">{t("section.timeline")}</h2>
        {series == null ? (
          <div className="notice" role="alert">
            {t("plan.seriesFailed", { subject: subjectNoun })}
          </div>
        ) : planned && "why" in planned ? (
          <div className="notice">{planned.why}</div>
        ) : points && points.length === 0 ? (
          <div className="notice">
            {t("plan.noBuckets", {
              subject: subjectNoun,
              what:
                f.unit === "share"
                  ? t("plan.shareWord")
                  : f.unit === "ports"
                    ? t("plan.portsWord")
                    : t("plan.eventsWord"),
            })}
          </div>
        ) : (
          points &&
          planned &&
          "plan" in planned &&
          f.unit != null && (
            <>
              {counts && counts.notExact > 0 && (
                <Banner word={t("banner.partial")}>
                  {t("banner.partialBody", {
                    notExact: grouped(counts.notExact, locale),
                    stored: grouped(counts.stored, locale),
                    extra:
                      (counts.notMeasured > 0
                        ? t("banner.partialNotMeasured", { count: grouped(counts.notMeasured, locale) })
                        : "") +
                      (counts.lowerBound > 0
                        ? t("banner.partialLowerBound", { count: grouped(counts.lowerBound, locale) })
                        : ""),
                  })}
                </Banner>
              )}
              {counts && counts.admissionTruncated > 0 && (
                <Banner word={t("banner.truncated")}>
                  {t("banner.truncatedBody", {
                    count: grouped(counts.admissionTruncated, locale),
                    stored: grouped(counts.stored, locale),
                  })}
                </Banner>
              )}
              {counts && counts.maintenance > 0 && (
                <p className="subtitle">
                  {t("banner.maintenanceBody", { count: counts.maintenance })}
                </p>
              )}
              {baselines == null && metric && (
                <Banner word={t("banner.baselineUnavailable")}>
                  {t("banner.baselineUnavailableBody")}
                </Banner>
              )}
              {noReliableCell && (
                <Banner word={wordOrValue(i18n, "enums.anomalyState", "INSUFFICIENT_DATA")}>
                  {t("banner.insufficientBody")}
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
                transitions={anomaly.transitions.map((tr) => ({
                  at: tr.occurred_at,
                  label: ipsStateWord(tr.to_state, i18n),
                }))}
                ariaLabel={t("chart.aria", { quantity })}
                textSummary={ipsChartSummary(anomaly, planned.plan.points, quantity, i18n)}
              />
            </>
          )
        )}
      </section>

      <section>
        <h2 className="section-title">{t("section.judged")}</h2>
        <IpsDetectionBlock anomaly={anomaly} bucketSeconds={bucketSeconds} />
      </section>

      <section>
        <h2 className="section-title">{t("section.evidence")}</h2>
        <p className="subtitle">{t("section.evidenceBody")}</p>
      </section>

      <section>
        <h2 className="section-title">{t("section.lifecycle")}</h2>
        <LifecycleTimeline
          transitions={anomaly.transitions}
          policyVersion={anomaly.policy_version}
        />
      </section>
    </>
  );
}
