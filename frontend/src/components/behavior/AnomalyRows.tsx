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

import { useTranslations } from "next-intl";
import { Fragment, useState } from "react";

import { useEnumLabels } from "@/i18n/enumLabels";
import { unitGap } from "@/i18n/measures";
import { sevTone, type AnomalySummary } from "@/lib/api";
import {
  detectorLabel,
  evidenceTone,
  formatDelta,
  formatDuration,
  formatMetric,
  formatPercentDelta,
  formatRatio,
  stateTone,
} from "@/lib/behavior";
import { ageSeconds, changePercent, isNoEvents, silenceSeconds } from "@/lib/anomalyList";
import { ARROW, evidenceWord, type BehaviorI18n } from "@/lib/behaviorI18n";
import { formatCount } from "@/lib/format";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import "@/components/ips/ips.css";
import { expectsNone, ipsFigures } from "@/components/ips/ipsDetectors";
import { formatFigure } from "@/components/ips/ipsFormat";
import { ipsSubjectText, isIpsRow, rowTypeLabel } from "@/components/ips/ipsLabels";
import { isPwRow, pwTypeLabel } from "@/components/propertywatch/pwFormat";
import { valueText } from "@/components/propertywatch/WatchValuesTable";
import {
  eventKey,
  formatEvents,
  identityHref,
  identityTypeLabel,
  isCatalogueEvent,
  isIdentityRow,
} from "@/components/identity/identityFormat";
import { formatMatches, isRuleRow, ruleTypeLabel } from "@/components/rules/ruleMatchFormat";
import { formatRequests, listUnit, sinceUnit } from "@/components/waf/wafFormat";
import { formatPercent } from "@/lib/format";
import {
  anomalyTypeLabel,
  endpointLabel,
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
function SubjectCell({ a, nowMs }: { a: AnomalySummary; nowMs: number }) {
  const t = useTranslations("anomalies.rows");
  const identityEvents = useTranslations("identity.events");
  const ipsI18n = useBehaviorI18n();
  // A property-watch row has no source of its own: its watch names it.
  const source = a.log_source_name ?? a.log_source_id ?? a.pw_value?.watch_name ?? t("unnamed");
  // Below 1100 px the Started column folds under the subject.
  const age = ageSeconds(a, nowMs);
  const started = (
    <>
      <span className="cell-sub cell-fold fold-sm fold-started">
        {t.rich("started", {
          time: () => <UtcTime value={a.anomaly_start ?? a.detected_at} variant="compact" />,
        })}
      </span>
      {/* The age folds with the start (up to 1280 px). */}
      {age != null && (
        <span className="cell-sub cell-fold fold-started-age">
          {t("ageFold", { age: formatDuration(age, ipsI18n) })}
        </span>
      )}
    </>
  );
  if (isIdentityRow(a) && a.identity) {
    // The log source links to its identity page on this event; the event is named beneath it.
    return (
      <td className="cell-name cell-subject">
        {a.log_source_id ? (
          <Link
            className="subject-source"
            href={identityHref(`/behavior/identity/sources/${a.log_source_id}`, { event: a.identity.event_id })}
            title={source}
          >
            {source}
          </Link>
        ) : (
          <span className="subject-source" title={source}>
            {source}
          </span>
        )}
        <span className="cell-sub subject-host">
          {isCatalogueEvent(a.identity.event_id)
            ? `${a.identity.event_id} ${identityEvents(eventKey(a.identity.event_id))}`
            : a.identity.event_id}
        </span>
        {started}
      </td>
    );
  }
  if (isRuleRow(a) && a.rule) {
    // The rule's name links to the rule; its QRadar id and the building-block marker sit beneath.
    return (
      <td className="cell-name cell-subject">
        <Link className="subject-source" href={`/rules/${a.rule.rule_id}`} title={a.rule.name}>
          {a.rule.name}
        </Link>
        <span className="cell-sub subject-host">
          {t("qradarId", { id: a.rule.qradar_id })}
          {a.rule.is_building_block ? ` · ${t("buildingBlock")}` : ""}
        </span>
        {started}
      </td>
    );
  }
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
        <span className="subject-endpoint ips-subject-name">{ipsSubjectText(a, ipsI18n)}</span>
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
        <span className="cell-sub subject-host">
          {t("virtualHost", { host: a.waf_endpoint.virtual_host })}
        </span>
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
  const t = useTranslations("anomalies.rows");
  const common = useTranslations("common");
  const units = useTranslations("units");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  if (isIdentityRow(a)) {
    // Events per 5-minute bucket. A null count is the dash, never 0; a floor says "at least".
    return (
      <>
        <span className="ips-figures">
          {a.observed_measurement === "LOWER_BOUND" && a.observed_value != null ? "≥ " : ""}
          {formatEvents(a.observed_value, locale)}
          <span className="muted"> {ARROW} </span>
          {formatEvents(a.expected_value, locale)}
        </span>
        <span className="cell-sub ips-unit">{t("eventsPerBucket")}</span>
        {a.observed_measurement === "LOWER_BOUND" && (
          <span className="cell-sub">{t("eventsTruncated")}</span>
        )}
      </>
    );
  }
  if (isRuleRow(a)) {
    // Matched events per collection window. A null count is the dash, never 0; a silence's
    // observed 0 is a measurement and stays "0".
    return (
      <>
        <span className="ips-figures">
          {a.observed_measurement === "LOWER_BOUND" && a.observed_value != null ? "≥ " : ""}
          {formatMatches(a.observed_value, locale)}
          <span className="muted"> {ARROW} </span>
          {formatMatches(a.expected_value, locale)}
        </span>
        <span className="cell-sub ips-unit">{t("matchesPerWindow")}</span>
        {a.observed_measurement === "LOWER_BOUND" && (
          <span className="cell-sub">{t("matchesTruncated")}</span>
        )}
      </>
    );
  }
  if (isPwRow(a)) {
    // Volume detectors count events per 5-minute bucket. The figure of a new-value detector has
    // no unit this list knows, so it shows none rather than a number under a guessed one.
    if (!isPwVolumeType(a.anomaly_type)) {
      return (
        <span className="muted">
          —<span className="sr-only"> {t("noBucketFigure")}</span>
        </span>
      );
    }
    return (
      <>
        <span className="ips-figures">
          {formatRequests(a.observed_value, a.observed_measurement, locale)}
          <span className="muted"> {ARROW} </span>
          {formatRequests(a.expected_value, null, locale)}
        </span>
        <span className="cell-sub ips-unit">{t("eventsPer5Min")}</span>
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
          —<span className="sr-only"> {t("unitUnknown")}</span>
        </span>
      );
    }
    const m = a.observed_measurement;
    // The figures are one line and the unit sits under them, so no unit is ever cut at the
    // column's edge. A new port expects none; the column header already says Expected.
    const unitWords =
      f.unit === "events"
        ? t("unitEvents")
        : f.unit === "ports"
          ? t("unitPorts")
          : f.unit === "since"
            ? t("unitSince")
            : null;
    return (
      <>
        <span className="ips-figures">
          {formatFigure(f.unit, f.observed, m, i18n)}
          <span className="muted"> {ARROW} </span>
          {expectsNone(a.anomaly_type) ? common("none") : formatFigure(f.unit, f.expected, undefined, i18n)}
        </span>
        {unitWords && <span className="cell-sub ips-unit">{unitWords}</span>}
      </>
    );
  }
  if (!isWafRow(a)) {
    return (
      <>
        {formatMetric(a.observed_value, 2, locale)}
        <span className="muted"> {ARROW} </span>
        {formatMetric(a.expected_value, 2, locale)}
        <span className="unit">
          {unitGap(locale)}
          {units("eps")}
        </span>
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
          {formatRequests(a.observed_value, a.observed_measurement, locale)}
          <span className="muted"> {ARROW} </span>
          {common("none")}
        </span>
        <span className="cell-sub ips-unit">{sinceUnit(i18n)}</span>
      </>
    );
  }
  return (
    <>
      {ratio
        ? formatPercent(a.observed_value, a.observed_measurement, 1, locale)
        : formatRequests(a.observed_value, a.observed_measurement, locale)}
      <span className="muted"> {ARROW} </span>
      {ratio
        ? formatPercent(a.expected_value, null, 1, locale)
        : formatRequests(a.expected_value, null, locale)}
      {!ratio && <span className="unit"> {listUnit(i18n)}</span>}
    </>
  );
}

/**
 * Evidence collection. A WAF anomaly is never given an evidence request:
 * `app/waf/engine.py` creates every one as NOT_REQUESTED and nothing changes it,
 * so a word there would read as something the analyst could ask for.
 */
function EvidenceCell({ a }: { a: AnomalySummary }) {
  const t = useTranslations("anomalies.rows");
  const i18n = useBehaviorI18n();
  if (isWafRow(a) || isIpsRow(a) || isPwRow(a) || isRuleRow(a)) {
    const meaning = t("evidenceNotApplicable", {
      family: isRuleRow(a) ? "rule" : isPwRow(a) ? "pw" : isIpsRow(a) ? "ips" : "waf",
    });
    return (
      <span className="muted" title={meaning}>
        —<span className="sr-only"> {meaning}</span>
      </span>
    );
  }
  return (
    <span className={`pill pill-quiet ${evidenceTone(a.evidence_status)}`}>
      {evidenceWord(i18n, a.evidence_status)}
    </span>
  );
}

/** An IPS delta has a unit only for the count detectors; a share's change is in points, not here. */
function ipsDeltaHidden(a: AnomalySummary): boolean {
  if (!isIpsRow(a)) return false;
  const unit = ipsFigures(a, null).unit;
  return unit !== "events" && unit !== "ports";
}

/** The detector's name in a row: Phase A in the interface language; the other families' own labels. */
function typeText(a: AnomalySummary, i18n: BehaviorI18n): string {
  if (isIdentityRow(a)) return identityTypeLabel(a.anomaly_type, i18n);
  if (isRuleRow(a)) return ruleTypeLabel(a.anomaly_type, i18n);
  if (isPwRow(a)) return pwTypeLabel(a.anomaly_type, i18n);
  if (isWafRow(a)) return anomalyTypeLabel(a.anomaly_type, i18n);
  if (!isIpsRow(a)) return detectorLabel(a.anomaly_type, i18n);
  return rowTypeLabel(a, i18n);
}

/**
 * The deviation cell: the multiplier with the percent change beside it. A NO_EVENTS row has no
 * ratio, so it says how long nothing has arrived instead of a dash.
 */
function DeviationCell({ a, nowMs }: { a: AnomalySummary; nowMs: number }) {
  const t = useTranslations("anomalies.rows");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  if (isNoEvents(a)) {
    const seconds = silenceSeconds(a, nowMs);
    return <>{seconds == null ? t("noEventsUnknown") : t("noEventsFor", { duration: formatDuration(seconds, i18n) })}</>;
  }
  const pct = changePercent(a);
  return (
    <>
      {formatRatio(a.deviation_ratio, locale)}
      {pct != null && <span className="muted"> {formatPercentDelta(pct, locale)}</span>}
    </>
  );
}

/** The analyst's triage status, as a quiet pill; a row without the block says it is not reported. */
function TriageCell({ a }: { a: AnomalySummary }) {
  const t = useTranslations("anomalies.rows");
  const tt = useTranslations("triage");
  if (!a.triage) return <span className="muted">{t("triageUnknown")}</span>;
  const s = a.triage.status;
  return (
    <>
      <span className={`pill pill-quiet ${s === "IN_PROGRESS" ? "info" : s === "NEW" ? "warn" : ""}`}>
        {tt(`status.${s}` as never)}
      </span>
      {a.triage.disposition && (
        <span className="cell-sub">{tt(`disposition.${a.triage.disposition}` as never)}</span>
      )}
    </>
  );
}

/**
 * A subject of at most this many characters is rendered as one word, with no break opportunity:
 * `td.cell-owner` has a 12ch floor, so such a subject always fits the Owner column unbroken, and
 * in the narrower fold line a short name ("m.chen") must move to the next line whole instead of
 * being split at its dot. 12 is also below what the fold line holds at 1024 px.
 */
export const OWNER_UNBROKEN_MAX = 12;

/**
 * An owner subject with break opportunities after "@", "." "-" and "_", so a long address wraps
 * at a natural point first. `<wbr>` adds no character: the text content stays the subject and a
 * copy gives the address. Only a subject longer than `OWNER_UNBROKEN_MAX` gets them. A single segment longer than the column still breaks anywhere
 * (`td.cell-owner`).
 */
function OwnerText({ value }: { value: string }) {
  if (value.length <= OWNER_UNBROKEN_MAX) return <>{value}</>;
  const parts = value.match(/[^@._-]*[@._-]?/g)?.filter((p) => p !== "") ?? [value];
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && <wbr />}
          {part}
        </Fragment>
      ))}
    </>
  );
}

export function AnomalyRows({ items, nowMs }: { items: AnomalySummary[]; nowMs: number }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const t = useTranslations("anomalies.rows");
  const tt = useTranslations("triage");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const { statePill, severityPill } = useEnumLabels();

  return (
    <tbody>
      {items.map((a) => {
        const open = expanded === a.id;
        return (
          <Fragment key={a.id}>
            <tr>
              <SubjectCell a={a} nowMs={nowMs} />
              <td className="cell-detector">
                {typeText(a, i18n)}
                {/* Below 1100 px severity folds under the detector. */}
                <span className="cell-sub cell-fold fold-sm">
                  {t("severityFold", { value: severityPill(a.severity) })}
                </span>
              </td>
              <td>
                <span className={`pill pill-strong ${stateTone(a.state)}`}>
                  {statePill(a.state)}
                </span>
                {/* Up to 1280 px the Triage and Owner columns fold under the state. */}
                <span className="cell-sub cell-fold fold-md">
                  {a.triage
                    ? t.rich("triageFoldLine", {
                        status: tt(`status.${a.triage.status}` as never),
                        // A long subject breaks at its natural points, as in the Owner cell.
                        // (next-intl renders a node value as is; its types only list tags.)
                        owner: (a.triage.owner ? (
                          <OwnerText value={a.triage.owner} />
                        ) : (
                          t("unassigned")
                        )) as never,
                      })
                    : t("triageUnknown")}
                </span>
                {/* Up to 1280 px the Evidence column folds under the state. */}
                {!isWafRow(a) && !isIpsRow(a) && !isPwRow(a) && !isRuleRow(a) && (
                  <span className="cell-sub cell-fold fold-md fold-ev">
                    {t("evidenceFold", { value: evidenceWord(i18n, a.evidence_status) })}
                  </span>
                )}
              </td>
              <td className="num">
                <ObservedCell a={a} />
                <span className="cell-sub cell-fold fold-md fold-dev">
                  {!isNoEvents(a) && `${t("deviationLabel")} `}
                  <DeviationCell a={a} nowMs={nowMs} />
                </span>
              </td>
              <td className="num col-md col-dev">
                <DeviationCell a={a} nowMs={nowMs} />
              </td>
              <td className="col-sm">
                <span className={`pill pill-quiet ${sevTone(a.severity)}`}>
                  {severityPill(a.severity)}
                </span>
              </td>
              <td className="num col-sm col-started">
                {<UtcTime value={a.anomaly_start ?? a.detected_at} variant="compact" />}
                {/* Up to 1440 px the Age column folds under the start. */}
                {ageSeconds(a, nowMs) != null && (
                  <span className="cell-sub cell-fold fold-age">
                    {t("ageFold", { age: formatDuration(ageSeconds(a, nowMs), i18n) })}
                  </span>
                )}
              </td>
              <td className="num col-sm col-started col-age">
                {(() => {
                  const age = ageSeconds(a, nowMs);
                  return age == null ? (
                    <span className="muted">—</span>
                  ) : (
                    formatDuration(age, i18n)
                  );
                })()}
              </td>
              <td className="col-md col-ev">
                <EvidenceCell a={a} />
              </td>
              <td className="col-md">
                <TriageCell a={a} />
              </td>
              <td className="col-md cell-owner">
                {!a.triage ? (
                  <span className="muted">{t("triageUnknown")}</span>
                ) : a.triage.owner ? (
                  <span title={a.triage.owner}>
                    <OwnerText value={a.triage.owner} />
                  </span>
                ) : (
                  <span className="muted">{t("unassigned")}</span>
                )}
              </td>
              {/* One actions cell rather than two columns: at 1024 px the
                  extra column was pushing the detail link out of view. */}
              <td className="row-actions">
                <div className="row-actions-inner">
                  <Link href={`/anomalies/${a.id}`}>{t("open")}</Link>
                  <button
                    type="button"
                    className="row-toggle"
                    aria-expanded={open}
                    aria-label={t(open ? "hideDetail" : "showDetail", {
                      type: typeText(a, i18n).toLocaleLowerCase(locale === "tr" ? "tr-TR" : "en-US"),
                      subject:
                        isIdentityRow(a) && a.identity
                          ? `${a.log_source_name ?? a.log_source_id ?? ""} · ${a.identity.event_id}`
                          : isRuleRow(a) && a.rule
                          ? `${a.rule.name} · ${t("qradarId", { id: a.rule.qradar_id })}`
                          : isPwRow(a) && a.pw_value
                            ? `${valueText(a.pw_value)} · ${a.pw_value.watch_name}`
                            : isIpsRow(a)
                              ? `${ipsSubjectText(a, i18n)} · ${a.log_source_name ?? a.log_source_id}`
                              : subjectText(a, i18n),
                    })}
                    onClick={() => setExpanded(open ? null : a.id)}
                  >
                    {open ? t("less") : t("more")}
                  </button>
                </div>
              </td>
            </tr>

            {open && (
              <tr className="row-detail">
                <td colSpan={12}>
                  <dl className="dimension-meta">
                    {/* A ratio detector's delta is a share, not a count, and the
                        list does not carry its unit: the investigation page does. A
                        new endpoint expects none, so it has no delta against expected. */}
                    {!(isPwRow(a) && !isPwVolumeType(a.anomaly_type)) &&
                      !(isWafRow(a) && (isRatioType(a.anomaly_type) || isNewEndpointType(a.anomaly_type))) &&
                      !ipsDeltaHidden(a) && (
                      <div>
                        <dt>{t("absoluteDelta")}</dt>
                        <dd className="num">
                          {a.absolute_delta == null ? (
                            formatDelta(a.absolute_delta, locale)
                          ) : (
                            <>
                              {formatDelta(a.absolute_delta, locale)}
                              <span className="muted">
                                {" "}
                                {isWafRow(a)
                                  ? t("deltaRequests")
                                  : isIpsRow(a) && ipsFigures(a, null).unit === "ports"
                                    ? t("deltaPorts")
                                    : t("deltaEvents")}
                              </span>
                            </>
                          )}
                        </dd>
                      </div>
                    )}
                    <div>
                      <dt>{t("duration")}</dt>
                      {/* Null while still running. Not "0s", and not "now
                          minus start", which would grow on every refresh. */}
                      <dd className="num">
                        {a.duration_seconds != null ? (
                          formatDuration(a.duration_seconds, i18n)
                        ) : (
                          <span className="muted">{t("running")}</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("confidence")}</dt>
                      <dd className="num">{formatMetric(a.confidence, 2, locale)}</dd>
                    </div>
                    <div>
                      <dt>{t("anomalyEnd")}</dt>
                      <dd className="num">
                        {a.anomaly_end ? (
                          <UtcTime value={a.anomaly_end} />
                        ) : (
                          <span className="muted">{t("stillRunning")}</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("resolved")}</dt>
                      <dd className="num">{<UtcTime value={a.resolved_at} />}</dd>
                    </div>
                    <div>
                      <dt>{t("consecutive")}</dt>
                      <dd className="num">{formatCount(a.consecutive_buckets, locale)}</dd>
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
