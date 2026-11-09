// A rule-match incident on the investigation page (SOC-6 design §7.2).
//
// Who and what, one summary sentence built from the stored figures, the measurements with their
// unit (matched events per window, windows, ×), and the lifecycle in UTC. The expected range is
// the incident's own, read from the rule's match-anomaly section (`band`): it is null when that
// read failed or the incident is no longer listed there, and the page then says so. No chart:
// the section on the rule's page owns the rule's history.

import type { ReactNode } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import "@/components/ui/controls.css";
import "./matchAnomalies.css";

import { PageHeader } from "@/components/ui/PageHeader";
import { UtcTime } from "@/components/ui/UtcTime";
import { guideFor } from "@/lib/guideRoutes";
import { sevTone, type RuleAnomalyDetail, type RuleMatchSectionIncident } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatCount } from "@/lib/format";
import { useEnumLabels } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";

import { formatMatches, formatTimes, MISSING } from "./ruleMatchFormat";

export type RuleBand =
  | { kind: "incident"; incident: RuleMatchSectionIncident }
  | { kind: "not-loaded" }
  | { kind: "not-listed" };

export function RuleInvestigation({
  anomaly,
  band,
  triage,
}: {
  anomaly: RuleAnomalyDetail;
  band: RuleBand;
  /** The analyst's triage block, built on the server (`triageNode`). */
  triage?: ReactNode;
}) {
  const t = useTranslations("anomalies.ruleInvestigation");
  const kinds = useTranslations("enums.ruleAnomalyType");
  const locale = useLocale() as Locale;
  const { statePill, severityPill } = useEnumLabels();

  const type = anomaly.anomaly_type;
  const detector = type === "RULE_SILENCE" || type === "RULE_STORM" ? kinds(type) : type;
  const observed = formatMatches(anomaly.observed_value, locale);
  const expected = formatMatches(anomaly.expected_value, locale);
  const summary =
    anomaly.anomaly_type === "RULE_STORM"
      ? t("summaryStorm", { observed, expected })
      : anomaly.anomaly_type === "RULE_SILENCE"
        ? t("summarySilence", { expected })
        : t("summaryOther", { observed, expected });
  const incident = band.kind === "incident" ? band.incident : null;
  const streak = incident?.consecutive_buckets ?? (anomaly.consecutive_buckets || null);

  return (
    <>
      <p className="muted">
        <Link href="/anomalies">{t("crumb")}</Link>
      </p>
      <PageHeader
        guide={guideFor("/anomalies/[id]")}
        title={anomaly.rule.name}
        description={t("description", {
          id: anomaly.rule.qradar_id,
          buildingBlock: anomaly.rule.is_building_block ? t("buildingBlockSuffix") : "",
        })}
      />
      <div className="row incident-identity">
        <span className="incident-detector">{detector}</span>
        <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>{statePill(anomaly.state)}</span>
        <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>{severityPill(anomaly.severity)}</span>
      </div>

      <p className="incident-summary">{summary}</p>
      {anomaly.observed_measurement === "LOWER_BOUND" && <p className="subtitle">{t("truncatedNote")}</p>}

      <section className="measure" aria-label={t("factsLabel")}>
        <dl className="measure-grid">
          <div className="measure-item measure-lead">
            <dt>{t("observed")}</dt>
            <dd className="measure-value">
              {anomaly.observed_measurement === "LOWER_BOUND" && anomaly.observed_value !== null ? "≥ " : ""}
              {observed}
              {anomaly.observed_value !== null && <span className="measure-unit">{t("perWindow")}</span>}
            </dd>
          </div>
          <div className="measure-item">
            <dt>{t("expected")}</dt>
            <dd className="measure-value">
              {expected}
              {anomaly.expected_value !== null && <span className="measure-unit">{t("perWindow")}</span>}
            </dd>
          </div>
          <div className="measure-item">
            <dt>{t("band")}</dt>
            <dd className="measure-value">
              {incident && incident.baseline_low !== null && incident.baseline_high !== null ? (
                <>
                  {t("bandValue", {
                    low: formatMatches(incident.baseline_low, locale),
                    high: formatMatches(incident.baseline_high, locale),
                  })}
                  <span className="measure-unit">{t("perWindow")}</span>
                </>
              ) : (
                MISSING
              )}
            </dd>
            {!(incident && incident.baseline_low !== null && incident.baseline_high !== null) && (
              <dd className="measure-note">
                {band.kind === "not-loaded" ? t("bandNotLoaded") : t("bandNotStored")}
              </dd>
            )}
          </div>
          <div className="measure-item measure-item-minor">
            <dt>{anomaly.anomaly_type === "RULE_SILENCE" ? t("streak") : t("ratio")}</dt>
            <dd className="measure-value">
              {anomaly.anomaly_type === "RULE_SILENCE" ? (
                streak === null ? (
                  MISSING
                ) : (
                  <>
                    {formatCount(streak, locale)}
                    <span className="measure-unit">{t("windows")}</span>
                  </>
                )
              ) : (
                formatTimes(incident?.ratio ?? anomaly.deviation_ratio, locale)
              )}
            </dd>
          </div>
        </dl>
      </section>

      <table className="kv-table rule-evidence" aria-label={t("facts")}>
        <tbody>
          <tr>
            <th scope="row">{t("started")}</th>
            <td>
              <UtcTime value={anomaly.anomaly_start ?? anomaly.detected_at} />
            </td>
          </tr>
          <tr>
            <th scope="row">{t("ended")}</th>
            <td>
              {anomaly.anomaly_end ? (
                <UtcTime value={anomaly.anomaly_end} />
              ) : (
                <span className="muted">{t("stillOpen")}</span>
              )}
            </td>
          </tr>
          <tr>
            <th scope="row">{t("resolved")}</th>
            <td>
              {anomaly.resolved_at ? <UtcTime value={anomaly.resolved_at} /> : <span className="muted">{MISSING}</span>}
            </td>
          </tr>
        </tbody>
      </table>

      <p>
        <Link href={`/rules/${anomaly.rule.rule_id}`}>{t("openRule")}</Link>
      </p>
      {triage}
    </>
  );
}
