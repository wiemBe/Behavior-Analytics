// What validation found, in the analyst's terms.
//
// Takes the stored report as data and the schema for labels. Every measure
// that may be missing is `null` in the contract and renders as an em dash with
// its meaning; a result that "says nothing about the field" is worded as such
// and never as the field failing.

import { useTranslations } from "next-intl";

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
import { plainCount } from "@/lib/figureText";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import "@/components/ui/controls.css";

// The label of each report status is `fieldMapping.report.<status>`.
const REPORT_STATUS: Record<string, { tone: string }> = {
  VALID: { tone: "ok" },
  INVALID: { tone: "crit" },
  INCONCLUSIVE: { tone: "warn" },
  UNVALIDATED: { tone: "" },
};

/** A figure that may be missing: the number, or a dash that says why. */
function Measure({ text, missing }: { text: string; missing: boolean }) {
  const t = useTranslations("fieldMapping.report");
  if (!missing) return <>{text}</>;
  return (
    <span title={t("notMeasured")}>
      {MISSING}
      <span className="sr-only">{t("notMeasuredSr")}</span>
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
  const t = useTranslations("fieldMapping.report");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const showDistinct = !hideColumns.includes("distinct");
  const showPlausible = !hideColumns.includes("plausible");
  const infoFor =
    statusInfo ??
    ((status: string, ctx: Parameters<typeof fieldStatusInfo>[1]) =>
      fieldStatusInfo(status, ctx, i18n));
  const stale = report !== null && report.profile_version !== profileVersion;
  const known = report !== null && Object.prototype.hasOwnProperty.call(REPORT_STATUS, report.status);
  const status = report
    ? {
        label: known ? t(report.status as "VALID") : report.status,
        tone: known ? REPORT_STATUS[report.status].tone : "",
      }
    : null;
  const version = plainCount(profileVersion, locale);

  return (
    <div className="fm-report">
      {pending && (
        <p className="fm-banner is-info" role="status">
          {pending.since
            ? t.rich("pendingSince", {
                version,
                time: () => <UtcTime value={pending.since}>{formatClock(pending.since)}</UtcTime>,
              })
            : t("pendingNoSince", { version })}
        </p>
      )}

      {report === null ? (
        <p className="fm-empty">{t("none", { version })}</p>
      ) : (
        <>
          {stale && (
            <p className="fm-banner is-warn" role="status">
              {t("stale", { reportVersion: plainCount(report.profile_version, locale), version })}
            </p>
          )}
          <dl className="fm-facts">
            <div>
              <dt>{t("result")}</dt>
              <dd>
                <span className={`pill ${status?.tone ?? ""}`}>{status?.label}</span>
              </dd>
            </div>
            <div>
              <dt>{t("window")}</dt>
              <dd className="num">
                <UtcTime value={report.window_start} />
                {t("to")}
                <UtcTime value={report.window_end} />
              </dd>
            </div>
            <div>
              <dt>{t("events")}</dt>
              <dd className="num">
                <Measure text={formatCount(report.total_events, locale)} missing={report.total_events == null} />
              </dd>
            </div>
            <div>
              <dt>{t("versionChecked")}</dt>
              <dd className="num">{plainCount(report.profile_version, locale)}</dd>
            </div>
          </dl>

          {report.reasons.length > 0 && (
            <ul className="fm-reasons" aria-label={t("reasonsLabel")}>
              {report.reasons.map((r) => (
                <li key={r}>
                  {(
                    formatReason ??
                    ((x, ratio) => reasonText(x, schema, requiredCoverage, ratio, i18n))
                  )(
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

          <TableScroll label={t("tableLabel")}>
            <table className="table-dense">
              <caption className="sr-only">{t("caption")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("field")}</th>
                  <th scope="col">{t("result")}</th>
                  <th scope="col" className="num">
                    {t("populated")}
                    <span className="cell-sub">{t("ofEvents")}</span>
                  </th>
                  {showDistinct && (
                    <th scope="col" className="num">
                      {t("distinct")}
                    </th>
                  )}
                  {showPlausible && (
                    <th scope="col" className="num">
                      {t("plausible")}
                      <span className="cell-sub">{t("ofSampled")}</span>
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
                          {f.property_name ? <code>{f.property_name}</code> : t("noProperty")}
                          {describeReference?.(f) ? ` · ${describeReference(f)}` : ""}
                          {" · "}
                          {f.used_by_collection ? t("needed") : t("informational")}
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
                          text={formatShare(f.populated_ratio, locale)}
                          missing={f.populated_ratio == null}
                        />
                      </td>
                      {showDistinct && (
                        <td className="num">
                          <Measure
                            text={formatCount(f.distinct_values, locale)}
                            missing={f.distinct_values == null}
                          />
                        </td>
                      )}
                      {showPlausible && (
                        <td className="num">
                          <Measure
                            text={formatShare(f.plausible_ratio, locale)}
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
