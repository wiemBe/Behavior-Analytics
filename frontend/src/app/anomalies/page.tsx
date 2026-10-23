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

import { AnomalyFilters } from "@/components/behavior/AnomalyFilters";
import { AnomalyRows } from "@/components/behavior/AnomalyRows";
import { Pagination } from "@/components/Pagination";
import { isIpsRow } from "@/components/ips/ipsLabels";
import { isPwRow } from "@/components/propertywatch/pwFormat";
import { isWafRow } from "@/components/waf/wafLabels";
import { ReportDataAge } from "@/components/shell/ReportDataAge";
import { PageHeader } from "@/components/ui/PageHeader";
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
} from "@/lib/anomalyQuery";
import { sourcesAsOf } from "@/lib/home/asOf";

import "./anomalies.css";

export const metadata: Metadata = {
  title: "Anomalies",
};

export default async function AnomaliesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const raw = await searchParams;
  const q = parseAnomalyQuery(raw);
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
        subject_kind: subject.subject_kind || undefined,
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
        ? "You do not have permission to view anomalies. Ask an administrator for the read:anomalies grant."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Anomalies could not be loaded. The backend may be unreachable; reload the page to try again.";
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
        title="Anomalies"
        description="Open an anomaly to see what changed during its interval. Times are UTC."
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
          {filtered
            ? "No anomalies match these filters. This is a filtered view, not a statement about the fleet."
            : "No anomalies have been detected. Sources without an adequate baseline are not being judged — check the behavioral overview for how many."}
        </div>
      ) : (
        <>
          <TableScroll label="Detected anomalies">
            <table className="sticky-actions anomaly-list">
              <caption className="sr-only">
                Detected anomalies with lifecycle state, observed against
                expected volume, deviation, severity and evidence status.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Subject</th>
                  <th scope="col">Detector</th>
                  <th scope="col">State</th>
                  <th scope="col" className="num col-nowrap">
                    {hasWaf ? "Observed → Expected" : "Observed → Expected (EPS)"}
                  </th>
                  <th scope="col" className="num col-md col-dev">
                    Deviation
                  </th>
                  <th scope="col" className="col-sm">
                    Severity
                  </th>
                  <th scope="col" className="num col-sm col-started">
                    Started
                  </th>
                  <th scope="col" className="col-md">
                    Evidence
                  </th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
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
