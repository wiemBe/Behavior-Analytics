"use client";

// The anomaly list's rows, with a per-row technical disclosure.
//
// Nine default columns instead of eleven, and the fields that were pushing the
// table past the viewport — absolute delta, duration, confidence, end and
// resolved times, baseline and policy versions — move into a row that opens on
// demand. None of it is lost; it simply stops competing with the columns an
// analyst triages on.
//
// Visual weight is deliberately unequal across the three status fields.
// Lifecycle state is the fact that decides whether this needs work now, so it
// carries the strong badge; severity and evidence are real but subordinate and
// render quietly. All three state their value as text, so none of them depends
// on colour to be understood.

import { Fragment, useState } from "react";

import { sevTone, type AnomalySummary } from "@/lib/api";
import {
  evidenceTone,
  formatDelta,
  formatDuration,
  formatMetric,
  formatRatio,
  stateTone,
} from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";
import { formatPercent, formatRequests } from "@/components/waf/wafFormat";
import { anomalyTypeLabel, endpointLabel, isWafRow, subjectText } from "@/components/waf/wafLabels";
import { isRatioType } from "@/components/waf/wafSummary";

import "@/components/ui/controls.css";

/**
 * The first cell: what the anomaly is about.
 *
 * A WAF row reads `GET /portal/login · lab-waf-behavior-01`, with the virtual
 * host beneath when there is one; a Phase A row is its source name.
 */
function SubjectCell({ a }: { a: AnomalySummary }) {
  const source = a.log_source_name ?? a.log_source_id;
  if (!isWafRow(a) || !a.waf_endpoint) {
    return (
      <td className="cell-name">
        <a href={`/behavior/sources/${a.log_source_id}`}>{source}</a>
      </td>
    );
  }
  return (
    <td className="cell-name cell-subject">
      <span className="waf-endpoint">{endpointLabel(a.waf_endpoint)}</span>
      <span className="muted"> · </span>
      <a href={`/behavior/waf/sources/${a.log_source_id}`}>{source}</a>
      {a.waf_endpoint.virtual_host && (
        <span className="cell-sub">Virtual host {a.waf_endpoint.virtual_host}</span>
      )}
    </td>
  );
}

/** Observed → expected, in the unit of the row's detector. */
function ObservedCell({ a }: { a: AnomalySummary }) {
  if (!isWafRow(a)) {
    return (
      <>
        {formatMetric(a.observed_value)}
        <span className="muted"> → </span>
        {formatMetric(a.expected_value)}
      </>
    );
  }
  // A WAF row states its unit in the cell: volume detectors count requests,
  // ratio detectors measure a share of them.
  const ratio = isRatioType(a.anomaly_type);
  return (
    <>
      {ratio
        ? formatPercent(a.observed_value, a.observed_measurement)
        : formatRequests(a.observed_value, a.observed_measurement)}
      <span className="muted"> → </span>
      {ratio ? formatPercent(a.expected_value) : formatRequests(a.expected_value)}
      {!ratio && <span className="unit"> req</span>}
    </>
  );
}

export function AnomalyRows({ items }: { items: AnomalySummary[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <tbody>
      {items.map((a) => {
        const open = expanded === a.id;
        return (
          <Fragment key={a.id}>
            <tr>
              <SubjectCell a={a} />
              <td>{anomalyTypeLabel(a.anomaly_type)}</td>
              <td>
                <span className={`pill pill-strong ${stateTone(a.state)}`}>
                  {a.state}
                </span>
              </td>
              <td className="num">
                <ObservedCell a={a} />
              </td>
              <td className="num">{formatRatio(a.deviation_ratio)}</td>
              <td>
                <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                  {a.severity}
                </span>
              </td>
              <td className="num">
                {formatDateTimeCompact(a.anomaly_start ?? a.detected_at)}
              </td>
              <td>
                <span className={`pill pill-quiet ${evidenceTone(a.evidence_status)}`}>
                  {a.evidence_status}
                </span>
              </td>
              {/* One actions cell rather than two columns: at 1024 px the
                  extra column was pushing the detail link out of view. */}
              <td className="row-actions">
                <div className="row-actions-inner">
                  <a href={`/anomalies/${a.id}`}>Open</a>
                  <button
                    type="button"
                    className="row-toggle"
                    aria-expanded={open}
                    aria-label={`${open ? "Hide" : "Show"} technical detail for the ${anomalyTypeLabel(
                      a.anomaly_type,
                    ).toLowerCase()} on ${subjectText(a)}`}
                    onClick={() => setExpanded(open ? null : a.id)}
                  >
                    {open ? "Less" : "More"}
                  </button>
                </div>
              </td>
            </tr>

            {open && (
              <tr className="row-detail">
                <td colSpan={9}>
                  <dl className="dimension-meta">
                    {/* A ratio detector's delta is a share, not a count, and the
                        list does not carry its unit: the investigation page does. */}
                    {!(isWafRow(a) && isRatioType(a.anomaly_type)) && (
                      <div>
                        <dt>Absolute delta</dt>
                        <dd className="num">
                          {a.absolute_delta == null ? (
                            formatDelta(a.absolute_delta)
                          ) : (
                            <>
                              {formatDelta(a.absolute_delta)}
                              <span className="muted">
                                {isWafRow(a) ? " requests" : " events"}
                              </span>
                            </>
                          )}
                        </dd>
                      </div>
                    )}
                    <div>
                      <dt>Duration</dt>
                      {/* Null while still running. Not "0s", and not "now
                          minus start", which would grow on every refresh. */}
                      <dd className="num">
                        {a.duration_seconds != null ? (
                          formatDuration(a.duration_seconds)
                        ) : (
                          <span className="muted">running</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Confidence</dt>
                      <dd className="num">{formatMetric(a.confidence)}</dd>
                    </div>
                    <div>
                      <dt>Anomaly end</dt>
                      <dd className="num">
                        {a.anomaly_end ? (
                          formatDateTime(a.anomaly_end)
                        ) : (
                          <span className="muted">still running</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Resolved</dt>
                      <dd className="num">{formatDateTime(a.resolved_at)}</dd>
                    </div>
                    <div>
                      <dt>Consecutive buckets</dt>
                      <dd className="num">{a.consecutive_buckets}</dd>
                    </div>
                  </dl>
                </td>
              </tr>
            )}
          </Fragment>
        );
      })}
    </tbody>
  );
}
