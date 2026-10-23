"use client";

import Link from "next/link";

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
import "@/components/ips/ips.css";
import { expectsNone, ipsFigures } from "@/components/ips/ipsDetectors";
import { formatFigure } from "@/components/ips/ipsFormat";
import { ipsSubjectText, isIpsRow, rowTypeLabel } from "@/components/ips/ipsLabels";
import { isPwRow, pwTypeLabel } from "@/components/propertywatch/pwFormat";
import { valueText } from "@/components/propertywatch/WatchValuesTable";
import { formatPercent, formatRequests, SINCE_UNIT } from "@/components/waf/wafFormat";
import {
  endpointLabel,
  evidenceLabel,
  isWafRow,
  subjectText,
} from "@/components/waf/wafLabels";
import { isNewEndpointType, isRatioType } from "@/components/waf/wafSummary";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/propertywatch/propertywatch.css";
import "@/components/ui/controls.css";

/**
 * The first cell: what the anomaly is about, stacked so no line has to share its
 * width with another.
 *
 * A WAF row is `METHOD /path` in mono, the source link beneath, and the virtual
 * host beneath that when there is one. A source name is one unbroken line that
 * ellipsizes, never breaking at a hyphen; its full text is the `title`. The
 * separator the old single line carried is kept for assistive technology only.
 */
function SubjectCell({ a }: { a: AnomalySummary }) {
  // A property-watch row has no source of its own: its watch names it.
  const source = a.log_source_name ?? a.log_source_id ?? a.pw_value?.watch_name ?? "Unnamed subject";
  // Below 1100 px the Started column folds under the subject.
  const started = (
    <span className="cell-sub cell-fold fold-sm fold-started">
      Started {<UtcTime value={a.anomaly_start ?? a.detected_at} variant="compact" />}
    </span>
  );
  if (isPwRow(a) && a.pw_value) {
    // The value (attacker-controlled text: rendered as text, wrapping anywhere), then the watch
    // that tracks it. A watch scoped to a type or a group has no single source to name.
    return (
      <td className="cell-name cell-subject">
        <span className="subject-endpoint pw-value-text">{valueText(a.pw_value)}</span>
        <span className="sr-only"> · </span>
        <Link
          className="subject-source"
          href={`/behavior/watches/${a.pw_value.watch_id}`}
          title={a.pw_value.watch_name}
        >
          {a.pw_value.watch_name}
        </Link>
        {started}
      </td>
    );
  }
  if (isIpsRow(a)) {
    // The entity, or "Source-level", then the IPS source. The source is plain text:
    // the page that administers it needs a grant the list cannot know the caller has.
    return (
      <td className="cell-name cell-subject">
        <span className="subject-endpoint ips-subject-name">{ipsSubjectText(a)}</span>
        <span className="sr-only"> · </span>
        <span className="subject-source" title={source}>
          {source}
        </span>
        {started}
      </td>
    );
  }
  if (!isWafRow(a) || !a.waf_endpoint) {
    return (
      <td className="cell-name cell-subject">
        {a.log_source_id ? (
          <Link className="subject-source" href={`/behavior/sources/${a.log_source_id}`} title={source}>
            {source}
          </Link>
        ) : (
          <span className="subject-source" title={source}>
            {source}
          </span>
        )}
        {started}
      </td>
    );
  }
  return (
    <td className="cell-name cell-subject">
      <span className="waf-endpoint subject-endpoint">{endpointLabel(a.waf_endpoint)}</span>
      <span className="sr-only"> · </span>
      <Link
        className="subject-source"
        href={`/behavior/waf/sources/${a.log_source_id}`}
        title={source}
      >
        {source}
      </Link>
      {a.waf_endpoint.virtual_host && (
        <span className="cell-sub subject-host">Virtual host {a.waf_endpoint.virtual_host}</span>
      )}
      {started}
    </td>
  );
}

/** The PW detectors whose observed and expected are event counts per 5-minute bucket. */
function isPwVolumeType(type: string): boolean {
  return type === "PW_VALUE_SPIKE" || type === "PW_VALUE_DROP" || type === "PW_VALUE_DISAPPEARED";
}

/** The unit of a WAF volume figure in the list. The list does not carry the bucket
 *  width; every WAF bucket is five minutes (`bucket_seconds` defaults to 300). */
export const WAF_LIST_UNIT = "req / 5 min";

/** Observed → expected, each cell in the unit of its own row's detector. */
function ObservedCell({ a }: { a: AnomalySummary }) {
  if (isPwRow(a)) {
    // Volume detectors count events per 5-minute bucket. The figure of a new-value detector has
    // no unit this list knows, so it shows none rather than a number under a guessed one.
    if (!isPwVolumeType(a.anomaly_type)) {
      return (
        <span className="muted">
          —<span className="sr-only"> this detector has no per-bucket figure; open the incident</span>
        </span>
      );
    }
    return (
      <>
        <span className="ips-figures">
          {formatRequests(a.observed_value, a.observed_measurement)}
          <span className="muted"> → </span>
          {formatRequests(a.expected_value)}
        </span>
        <span className="cell-sub ips-unit">events per 5 min</span>
      </>
    );
  }
  if (isIpsRow(a)) {
    // The unit is the detector's own, from the table of IPS detectors; the list
    // does not carry the bucket width. A type with no known unit shows no figure
    // rather than a number under a guessed unit.
    const f = ipsFigures(a, null);
    if (f.unit == null) {
      return (
        <span className="muted">
          —<span className="sr-only"> the unit of this detector is not known to this build</span>
        </span>
      );
    }
    const m = a.observed_measurement;
    // The figures are one line and the unit sits under them, so no unit is ever cut at the
    // column's edge. A new port expects none; the column header already says Expected.
    const unitWords =
      f.unit === "events"
        ? "events / bucket"
        : f.unit === "ports"
          ? "distinct ports"
          : f.unit === "since"
            ? "events since first appeared"
            : null;
    return (
      <>
        <span className="ips-figures">
          {formatFigure(f.unit, f.observed, m)}
          <span className="muted"> → </span>
          {expectsNone(a.anomaly_type) ? "none" : formatFigure(f.unit, f.expected)}
        </span>
        {unitWords && <span className="cell-sub ips-unit">{unitWords}</span>}
      </>
    );
  }
  if (!isWafRow(a)) {
    return (
      <>
        {formatMetric(a.observed_value)}
        <span className="muted"> → </span>
        {formatMetric(a.expected_value)}
        <span className="unit"> EPS</span>
      </>
    );
  }
  // Volume detectors count requests; ratio detectors measure a share of them,
  // whose unit is the % in the figure itself.
  const ratio = isRatioType(a.anomaly_type);
  if (isNewEndpointType(a.anomaly_type)) {
    // Cumulative since the endpoint first appeared, and none expected: not a per-bucket rate.
    return (
      <>
        <span className="ips-figures">
          {formatRequests(a.observed_value, a.observed_measurement)}
          <span className="muted"> → </span>none
        </span>
        <span className="cell-sub ips-unit">{SINCE_UNIT}</span>
      </>
    );
  }
  return (
    <>
      {ratio
        ? formatPercent(a.observed_value, a.observed_measurement)
        : formatRequests(a.observed_value, a.observed_measurement)}
      <span className="muted"> → </span>
      {ratio ? formatPercent(a.expected_value) : formatRequests(a.expected_value)}
      {!ratio && <span className="unit"> {WAF_LIST_UNIT}</span>}
    </>
  );
}

/**
 * Evidence collection. A WAF anomaly is never given an evidence request:
 * `app/waf/engine.py` creates every one as NOT_REQUESTED and nothing changes it,
 * so a word there would read as something the analyst could ask for.
 */
function EvidenceCell({ a }: { a: AnomalySummary }) {
  if (isWafRow(a) || isIpsRow(a) || isPwRow(a)) {
    const meaning = `Evidence collection does not apply to ${isPwRow(a) ? "property watch" : isIpsRow(a) ? "IPS" : "WAF"} anomalies`;
    return (
      <span className="muted" title={meaning}>
        —<span className="sr-only"> {meaning}</span>
      </span>
    );
  }
  return (
    <span className={`pill pill-quiet ${evidenceTone(a.evidence_status)}`}>
      {evidenceLabel(a.evidence_status)}
    </span>
  );
}

/** An IPS delta has a unit only for the count detectors; a share's change is in points, not here. */
function ipsDeltaHidden(a: AnomalySummary): boolean {
  if (!isIpsRow(a)) return false;
  const unit = ipsFigures(a, null).unit;
  return unit !== "events" && unit !== "ports";
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
              <td className="cell-detector">
                {isPwRow(a) ? pwTypeLabel(a.anomaly_type) : rowTypeLabel(a)}
                {/* Below 1100 px severity folds under the detector. */}
                <span className="cell-sub cell-fold fold-sm">Severity {a.severity}</span>
              </td>
              <td>
                <span className={`pill pill-strong ${stateTone(a.state)}`}>
                  {a.state}
                </span>
                {/* Up to 1280 px the Evidence column folds under the state. */}
                {!isWafRow(a) && !isIpsRow(a) && !isPwRow(a) && (
                  <span className="cell-sub cell-fold fold-md">
                    Evidence: {evidenceLabel(a.evidence_status)}
                  </span>
                )}
              </td>
              <td className="num">
                <ObservedCell a={a} />
                <span className="cell-sub cell-fold fold-md fold-dev">
                  Deviation {formatRatio(a.deviation_ratio)}
                </span>
              </td>
              <td className="num col-md col-dev">{formatRatio(a.deviation_ratio)}</td>
              <td className="col-sm">
                <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                  {a.severity}
                </span>
              </td>
              <td className="num col-sm col-started">
                {<UtcTime value={a.anomaly_start ?? a.detected_at} variant="compact" />}
              </td>
              <td className="col-md">
                <EvidenceCell a={a} />
              </td>
              {/* One actions cell rather than two columns: at 1024 px the
                  extra column was pushing the detail link out of view. */}
              <td className="row-actions">
                <div className="row-actions-inner">
                  <Link href={`/anomalies/${a.id}`}>Open</Link>
                  <button
                    type="button"
                    className="row-toggle"
                    aria-expanded={open}
                    aria-label={`${open ? "Hide" : "Show"} technical detail for the ${(isPwRow(a) ? pwTypeLabel(a.anomaly_type) : rowTypeLabel(a)).toLowerCase()} on ${isPwRow(a) && a.pw_value ? `${valueText(a.pw_value)} · ${a.pw_value.watch_name}` : isIpsRow(a) ? `${ipsSubjectText(a)} · ${a.log_source_name ?? a.log_source_id}` : subjectText(a)}`}
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
                        list does not carry its unit: the investigation page does. A
                        new endpoint expects none, so it has no delta against expected. */}
                    {!(isPwRow(a) && !isPwVolumeType(a.anomaly_type)) &&
                      !(isWafRow(a) && (isRatioType(a.anomaly_type) || isNewEndpointType(a.anomaly_type))) &&
                      !ipsDeltaHidden(a) && (
                      <div>
                        <dt>Absolute delta</dt>
                        <dd className="num">
                          {a.absolute_delta == null ? (
                            formatDelta(a.absolute_delta)
                          ) : (
                            <>
                              {formatDelta(a.absolute_delta)}
                              <span className="muted">
                                {isWafRow(a)
                                  ? " requests"
                                  : isIpsRow(a) && ipsFigures(a, null).unit === "ports"
                                    ? " ports"
                                    : " events"}
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
                          <UtcTime value={a.anomaly_end} />
                        ) : (
                          <span className="muted">still running</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Resolved</dt>
                      <dd className="num">{<UtcTime value={a.resolved_at} />}</dd>
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
