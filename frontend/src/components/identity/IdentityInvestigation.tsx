// An identity incident on the investigation page (SOC-10f).
//
// Who and what: the log source and the catalogue event, one summary sentence built from the stored
// figures, the measurements with their unit (events per 5 minutes), the stored top-N names the
// incident was raised with, and the lifecycle in UTC. A count the API did not send is the dash; a
// floor says "at least". Usernames are exactly the stored rows, rendered as text, and link to the
// entity timeline. Nothing here recomputes a verdict.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel, useEnumLabels, type EnumTranslator } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { sevTone, type IdentityAnomalyDetail } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { entityHref } from "@/lib/entities";
import { formatCount } from "@/lib/format";
import { guideFor } from "@/lib/guideRoutes";

import {
  MISSING,
  eventKey,
  formatEvents,
  identityHref,
  isCatalogueEvent,
  isVolumeEvent,
  TOP_N,
  VOLUME_NAME_FLOOR,
} from "./identityFormat";

import "@/components/ui/controls.css";
import "./identity.css";

export function IdentityInvestigation({
  anomaly,
  triage,
  quiet,
  related,
  pivot,
}: {
  anomaly: IdentityAnomalyDetail;
  triage?: ReactNode;
  quiet?: ReactNode;
  related?: ReactNode;
  pivot?: ReactNode;
}) {
  const t = useTranslations("identity.investigation");
  const events = useTranslations("identity.events");
  const enums = useTranslations("enums") as unknown as EnumTranslator;
  const locale = useLocale() as Locale;
  const { statePill, severityPill } = useEnumLabels();

  const eventId = anomaly.identity.event_id;
  const eventName = isCatalogueEvent(eventId) ? events(eventKey(eventId)) : "";
  const event = eventName ? `${eventId} ${eventName}` : String(eventId);
  const type = anomaly.anomaly_type;
  const detector = enumLabel(enums, "identityAnomalyType", type);
  const floor = anomaly.observed_measurement === "LOWER_BOUND";
  const observed = formatEvents(anomaly.observed_value, locale);
  const expected = formatEvents(anomaly.expected_value, locale);
  const summary =
    anomaly.observed_value === null
      ? t("summaryNoCount", { event })
      : anomaly.expected_value === null
        ? t("summaryNoExpected", { event, observed: `${floor ? "≥ " : ""}${observed}` })
        : t("summary", { event, observed: `${floor ? "≥ " : ""}${observed}`, expected });
  const names = anomaly.identity.usernames;
  const sourceHref = anomaly.log_source_id
    ? identityHref(`/behavior/identity/sources/${anomaly.log_source_id}`, { event: eventId })
    : null;

  return (
    <>
      <p className="muted">
        <Link href="/anomalies">{t("crumb")}</Link>
      </p>
      <PageHeader
        guide={guideFor("/anomalies/[id]")}
        title={anomaly.log_source_name ?? t("unnamed")}
        description={t("description", { event })}
      />
      <div className="row incident-identity">
        <span className="incident-detector">{detector}</span>
        <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>{statePill(anomaly.state)}</span>
        <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>{severityPill(anomaly.severity)}</span>
      </div>

      <p className="incident-summary">{summary}</p>
      {floor && <p className="subtitle">{t("floorNote")}</p>}

      <section className="measure" aria-label={t("factsLabel")}>
        <dl className="measure-grid">
          <div className="measure-item measure-lead">
            <dt>{t("observed")}</dt>
            <dd className="measure-value">
              {floor && anomaly.observed_value !== null ? "≥ " : ""}
              {observed}
              {anomaly.observed_value !== null && <span className="measure-unit">{t("perBucket")}</span>}
            </dd>
          </div>
          <div className="measure-item">
            <dt>{t("expected")}</dt>
            <dd className="measure-value">
              {expected}
              {anomaly.expected_value !== null && <span className="measure-unit">{t("perBucket")}</span>}
            </dd>
            {anomaly.expected_value === null && <dd className="measure-note">{t("noExpected")}</dd>}
          </div>
          <div className="measure-item measure-item-minor">
            <dt>{t("ratio")}</dt>
            <dd className="measure-value">
              {anomaly.deviation_ratio === null ? MISSING : `${formatEvents(anomaly.deviation_ratio, locale)} ×`}
            </dd>
          </div>
          <div className="measure-item measure-item-minor">
            <dt>{t("streak")}</dt>
            <dd className="measure-value">
              {formatCount(anomaly.consecutive_buckets, locale)}
              <span className="measure-unit">{t("buckets")}</span>
            </dd>
          </div>
        </dl>
      </section>

      <section className="stack-6 identity-evidence" aria-labelledby="identity-evidence-title">
        <h2 className="section-title" id="identity-evidence-title">
          {t("namesHeading")}
        </h2>
        {anomaly.evidence_status === "PARTIAL" && (
          <p className="banner warn" role="status">
            {t("namesPartial")}
          </p>
        )}
        {names.length === 0 ? (
          <div className="notice" role="status">
            <p>{isVolumeEvent(eventId) ? t("noNamesVolume", { floor: VOLUME_NAME_FLOOR }) : t("noNames")}</p>
          </div>
        ) : (
          <>
            <TableScroll label={t("namesLabel")}>
              <table className="table-dense">
                <caption className="sr-only">{t("namesCaption", { event })}</caption>
                <thead>
                  <tr>
                    <th scope="col" className="num">
                      {t("rank")}
                    </th>
                    <th scope="col">{t("username")}</th>
                    <th scope="col" className="num">
                      {t("events")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {names.map((n) => (
                    <tr key={`${n.rank}-${n.username}`}>
                      <td className="num">{formatCount(n.rank, locale)}</td>
                      <td className="cell-name identity-name">
                        <Link href={entityHref({ kind: "user", value: n.username })}>{n.username}</Link>
                      </td>
                      <td className="num">{formatCount(n.count, locale)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
            <p className="subtitle">{t("namesNote", { top: TOP_N })}</p>
          </>
        )}
      </section>

      <table className="kv-table identity-evidence" aria-label={t("facts")}>
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
              {anomaly.anomaly_end ? <UtcTime value={anomaly.anomaly_end} /> : <span className="muted">{t("stillOpen")}</span>}
            </td>
          </tr>
          <tr>
            <th scope="row">{t("resolved")}</th>
            <td>
              {anomaly.resolved_at ? <UtcTime value={anomaly.resolved_at} /> : <span className="muted">{MISSING}</span>}
            </td>
          </tr>
          {anomaly.detection?.score_status && (
            <tr>
              <th scope="row">{t("score")}</th>
              <td>{anomaly.detection.score_status}</td>
            </tr>
          )}
        </tbody>
      </table>

      {sourceHref && (
        <p>
          <Link href={sourceHref}>{t("openSource")}</Link>
        </p>
      )}
      {triage}
      {quiet}
      {related}
      {pivot}
    </>
  );
}
