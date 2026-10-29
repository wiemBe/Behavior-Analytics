// Anomaly list: every detected deviation, filterable and server-paginated.
//
// Deliberately distinguishes three outcomes that a naive list collapses into
// one blank table: no anomalies exist, no anomalies match these filters, and
// the request failed. Only the first is good news.
//
// Nine default columns, down from eleven. Absolute delta, duration,
// confidence, the end and resolved timestamps and both version numbers moved
// into a per-row disclosure — they were widening the table past the viewport
// at 1024 px, where the Started column was clipped mid-word and Evidence and
// the detail link were off-screen entirely.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AnomalyFilters } from "@/components/behavior/AnomalyFilters";
import { AnomalyRows } from "@/components/behavior/AnomalyRows";
import { Pagination } from "@/components/Pagination";
import { isIpsRow } from "@/components/ips/ipsLabels";
import { isPwRow } from "@/components/propertywatch/pwFormat";
import { isWafRow } from "@/components/waf/wafLabels";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  ApiError,
  api,
  type AnomalySummary,
  type Page,
  type SourceBehavior,
} from "@/lib/api";
import {
  PAGE_SIZE,
  paginationParams,
  parseAnomalyQuery,
  parseSubjectQuery,
  subjectKindsForApi,
  type SearchParamValue,
} from "@/lib/anomalyQuery";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { sourcesAsOf } from "@/lib/home/asOf";
import { withRouteMessages } from "@/i18n/RouteMessages";

import "./anomalies.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("anomalies.list");
  return { title: t("metaTitle") };
}

async function AnomaliesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, SearchParamValue>>;
}) {
  const raw = await searchParams;
  const t = await getTranslations("anomalies.list");
  const i18n = await getBehaviorI18n();
  const q = parseAnomalyQuery(raw, i18n);
  // The subject filters are parsed beside the Phase A query, not in it: a WAF
  // detector type is a valid `anomaly_type` here and would be dropped by the
  // Phase A allow-list.
  const subject = parseSubjectQuery(raw);
  const anomalyType =
    q.anomaly_type ||
    subject.waf_anomaly_type ||
    subject.ips_anomaly_type ||
    subject.pw_anomaly_type;

  let page: Page<AnomalySummary> | null = null;
  let sources: SourceBehavior[] = [];
  let sourcesFailed = false;
  let error: string | null = null;

  try {
    // The source list only populates the filter dropdown, so its failure must
    // not blank the table; it is fetched alongside but tolerated separately.
    const [listed, behaviors] = await Promise.all([
      api.anomalies({
        limit: PAGE_SIZE,
        offset: q.offset,
        active_only: q.active_only || undefined,
        log_source_id: q.log_source_id || undefined,
        instance_id: q.instance_id || undefined,
        anomaly_type: anomalyType || undefined,
        subject_kind: subjectKindsForApi(subject.subject_kind),
        ips_entity_id: subject.ips_entity_id || undefined,
        property_watch_id: subject.property_watch_id || undefined,
        state: q.state || undefined,
        severity: q.severity || undefined,
        evidence_status: q.evidence_status || undefined,
        since: q.since || undefined,
        until: q.until || undefined,
      }),
      api.sourceBehaviors().catch(() => {
        sourcesFailed = true;
        return [] as SourceBehavior[];
      }),
    ]);
    page = listed;
    sources = behaviors;
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? t("forbidden")
        : err instanceof ApiError && err.status === 401
          ? t("expired")
          : t("failed");
  }

  const filtered = Boolean(
    q.active_only ||
      q.log_source_id ||
      q.instance_id ||
      anomalyType ||
      subject.subject_kind ||
      subject.ips_entity_id ||
      subject.property_watch_id ||
      q.state ||
      q.severity ||
      q.evidence_status ||
      q.since ||
      q.until,
  );
  const items = page?.items ?? [];
  // Every cell of the observed column names its own unit (EPS, req / 5 min, events,
  // ports or %), so the heading carries none once a WAF or IPS row is on the page. A
  // page of log sources keeps the "(EPS)" its existing test pins.
  const hasWaf = items.some((a) => isWafRow(a) || isIpsRow(a) || isPwRow(a));
  // The source list reads the Phase A volume collection: it describes the data only when the
  // list is pinned to log sources. An unfiltered list also holds WAF, IPS and watch rows.
  const dataTo = subject.subject_kind === "LOG_SOURCE" ? sourcesAsOf(sources, null) : null;
  return (
    <>
      <ReportDataAge iso={dataTo} />
      <PageHeader
        guide={guideFor("/anomalies")}
        title={t("title")}
        description={t("description")}
      />

      <AnomalyFilters
        values={{
          subject_kind: subject.subject_kind,
          ips_entity_id: subject.ips_entity_id,
          property_watch_id: subject.property_watch_id,
          active_only: q.active_only,
          log_source_id: q.log_source_id,
          instance_id: q.instance_id,
          anomaly_type: anomalyType,
          state: q.state,
          severity: q.severity,
          evidence_status: q.evidence_status,
          since: q.since,
          until: q.until,
        }}
        sources={sources}
        sourcesFailed={sourcesFailed}
      />

      {q.rangeNote && <div className="notice">{q.rangeNote}</div>}

      {error ? (
        <div className="notice" role="alert">
          {error}
        </div>
      ) : items.length === 0 ? (
        <div className="notice">
          {filtered ? t("noMatch") : t("none")}
        </div>
      ) : (
        <>
          <TableScroll label={t("label")}>
            <table className="sticky-actions anomaly-list">
              <caption className="sr-only">
                {t("caption")}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("subject")}</th>
                  <th scope="col">{t("detector")}</th>
                  <th scope="col">{t("state")}</th>
                  <th scope="col" className="num col-nowrap">
                    {hasWaf ? t("observedExpected") : t("observedExpectedEps")}
                  </th>
                  <th scope="col" className="num col-md col-dev">
                    {t("deviation")}
                  </th>
                  <th scope="col" className="col-sm">
                    {t("severity")}
                  </th>
                  <th scope="col" className="num col-sm col-started">
                    {t("started")}
                  </th>
                  <th scope="col" className="col-md">
                    {t("evidence")}
                  </th>
                  <th scope="col">
                    <span className="sr-only">{t("actions")}</span>
                  </th>
                </tr>
              </thead>
              <AnomalyRows items={items} />
            </table>
          </TableScroll>

          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={q.offset}
            basePath="/anomalies"
            params={{
              ...paginationParams(q),
              anomaly_type: anomalyType || undefined,
              subject_kind: subject.subject_kind || undefined,
              ips_entity_id: subject.ips_entity_id || undefined,
              property_watch_id: subject.property_watch_id || undefined,
            }}
          />
        </>
      )}
    </>
  );
}

export default withRouteMessages("/anomalies", AnomaliesPage);
