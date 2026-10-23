// What validation found, in the analyst's terms.
//
// Takes the stored report as data and the schema for labels. Every measure
// that may be missing is `null` in the contract and renders as an em dash with
// its meaning; a result that "says nothing about the field" is worded as such
// and never as the field failing.

import { TableScroll } from "@/components/ui/TableScroll";
import type { FieldValidationOut, MappingSchemaOut, ValidationReportOut } from "@/lib/api";
import {
  type FieldStatusInfo,
  MISSING,
  fieldStatusInfo,
  formatClock,
  formatCount,
  formatShare,
  reasonText,
} from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

const REPORT_STATUS: Record<string, { label: string; tone: string }> = {
  VALID: { label: "Valid", tone: "ok" },
  INVALID: { label: "Invalid", tone: "crit" },
  INCONCLUSIVE: { label: "Inconclusive", tone: "warn" },
  UNVALIDATED: { label: "Not validated", tone: "" },
};

/** A figure that may be missing: the number, or a dash that says why. */
function Measure({ text, missing }: { text: string; missing: boolean }) {
  if (!missing) return <>{text}</>;
  return (
    <span title="Not measured">
      {MISSING}
      <span className="sr-only"> not measured</span>
    </span>
  );
}

export function ValidationReport({
  schema,
  report,
  profileVersion,
  pending,
  requiredCoverage,
  notes = [],
  hideColumns = [],
  statusInfo,
  formatReason,
  describeReference,
}: {
  schema: MappingSchemaOut;
  report: ValidationReportOut | null;
  /** The version the profile is at now. */
  profileVersion: number;
  /** Set while a validation is running. */
  pending: { since: string | null } | null;
  /** The share of events a collection field must populate, when known. */
  requiredCoverage: number | null;
  /** Warnings that are not failures, shown above the table (for example a generic-name share). */
  notes?: { key: string; text: string }[];
  /** Columns a domain does not measure; they are left out rather than shown as dashes. */
  hideColumns?: ("distinct" | "plausible")[];
  /** A domain's own status words; the default covers the WAF statuses. */
  statusInfo?: (status: string, ctx: Parameters<typeof fieldStatusInfo>[1]) => FieldStatusInfo;
  /** A domain's own sentence for a reason; the default covers the WAF reasons. */
  formatReason?: (reason: string, populatedRatio: (field: string) => number | null) => string;
  /** What follows the property name in the field cell, for example its reference kind. */
  describeReference?: (field: FieldValidationOut) => string | null;
}) {
  const showDistinct = !hideColumns.includes("distinct");
  const showPlausible = !hideColumns.includes("plausible");
  const infoFor = statusInfo ?? fieldStatusInfo;
  const stale = report !== null && report.profile_version !== profileVersion;
  const status = report ? (REPORT_STATUS[report.status] ?? { label: report.status, tone: "" }) : null;

  return (
    <div className="fm-report">
      {pending && (
        <p className="fm-banner is-info" role="status">
          Validating version {profileVersion}
          {pending.since ? (
            <>
              {" "}
              since{" "}
              <UtcTime value={pending.since}>{formatClock(pending.since)}</UtcTime>
            </>
          ) : null}
          . This page checks every 5 seconds.
        </p>
      )}

      {report === null ? (
        <p className="fm-empty">
          Version {profileVersion} has not been validated, so there is no report. Validation reads
          a bounded sample of this source’s events from QRadar.
        </p>
      ) : (
        <>
          {stale && (
            <p className="fm-banner is-warn" role="status">
              This report is for version {report.profile_version}; version {profileVersion} has not
              been validated.
            </p>
          )}
          <dl className="fm-facts">
            <div>
              <dt>Result</dt>
              <dd>
                <span className={`pill ${status?.tone ?? ""}`}>{status?.label}</span>
              </dd>
            </div>
            <div>
              <dt>Window (UTC)</dt>
              <dd className="num">
                <UtcTime value={report.window_start} />
                {" to "}
                <UtcTime value={report.window_end} />
              </dd>
            </div>
            <div>
              <dt>Events in window</dt>
              <dd className="num">
                <Measure text={formatCount(report.total_events)} missing={report.total_events == null} />
              </dd>
            </div>
            <div>
              <dt>Version checked</dt>
              <dd className="num">{report.profile_version}</dd>
            </div>
          </dl>

          {report.reasons.length > 0 && (
            <ul className="fm-reasons" aria-label="Reasons for the result">
              {report.reasons.map((r) => (
                <li key={r}>
                  {(formatReason ?? ((x, ratio) => reasonText(x, schema, requiredCoverage, ratio)))(
                    r,
                    (k) => report.fields.find((x) => x.field === k)?.populated_ratio ?? null,
                  )}
                </li>
              ))}
            </ul>
          )}

          {notes.map((n) => (
            <p key={n.key} className="fm-banner is-warn" role="status">
              {n.text}
            </p>
          ))}

          <TableScroll label="Validation result per field">
            <table className="table-dense">
              <caption className="sr-only">
                What validation found for each mapped field. Shares are percentages of the events
                in the window.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Result</th>
                  <th scope="col" className="num">
                    Populated
                    <span className="cell-sub">% of events</span>
                  </th>
                  {showDistinct && (
                    <th scope="col" className="num">
                      Distinct values
                    </th>
                  )}
                  {showPlausible && (
                    <th scope="col" className="num">
                      Plausible
                      <span className="cell-sub">% of sampled</span>
                    </th>
                  )}
                </tr>
              </thead>
              <tbody>
                {report.fields.map((f) => {
                  const info = infoFor(f.status, {
                    populatedRatio: f.populated_ratio,
                    plausibleRatio: f.plausible_ratio,
                    requiredCoverage,
                    usedByCollection: f.used_by_collection,
                  });
                  const def = schema.fields.find((x) => x.key === f.field);
                  return (
                    <tr key={f.field}>
                      <th scope="row" className="fm-field-cell">
                        {def?.label ?? f.field}
                        <span className="cell-sub">
                          {f.property_name ? <code>{f.property_name}</code> : "no property mapped"}
                          {describeReference?.(f) ? ` · ${describeReference(f)}` : ""}
                          {" · "}
                          {f.used_by_collection ? "collection needs it" : "informational"}
                        </span>
                      </th>
                      <td className="fm-result-cell">
                        <span className={`pill pill-quiet ${info.tone === "neutral" ? "" : info.tone}`}>
                          {info.label}
                        </span>
                        <span className="cell-sub fm-sentence">{info.sentence}</span>
                        {f.detail && <span className="cell-sub fm-sentence">{f.detail}</span>}
                      </td>
                      <td className="num">
                        <Measure
                          text={formatShare(f.populated_ratio)}
                          missing={f.populated_ratio == null}
                        />
                      </td>
                      {showDistinct && (
                        <td className="num">
                          <Measure
                            text={formatCount(f.distinct_values)}
                            missing={f.distinct_values == null}
                          />
                        </td>
                      )}
                      {showPlausible && (
                        <td className="num">
                          <Measure
                            text={formatShare(f.plausible_ratio)}
                            missing={f.plausible_ratio == null}
                          />
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
        </>
      )}
    </div>
  );
}
