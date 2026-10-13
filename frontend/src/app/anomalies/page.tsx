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
import { parseSubjectQuery } from "@/components/waf/subjectQuery";
import { isWafRow } from "@/components/waf/wafLabels";
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
} from "@/lib/anomalyQuery";

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
  const anomalyType = q.anomaly_type || subject.waf_anomaly_type;

  let page: Page<AnomalySummary> | null = null;
  let sources: SourceBehavior[] = [];
  let error: string | null = null;

  try {
    // The source list only populates the filter dropdown, so its failure must
    // not blank the table; it is fetched alongside but tolerated separately.
    const [listed, behaviors] = await Promise.all([
      api.anomalies({
        limit: PAGE_SIZE,
        offset: q.offset,
        log_source_id: q.log_source_id || undefined,
        instance_id: q.instance_id || undefined,
        anomaly_type: anomalyType || undefined,
        subject_kind: subject.subject_kind || undefined,
        state: q.state || undefined,
        severity: q.severity || undefined,
        evidence_status: q.evidence_status || undefined,
        since: q.since || undefined,
        until: q.until || undefined,
      }),
      api.sourceBehaviors().catch(() => [] as SourceBehavior[]),
    ]);
    page = listed;
    sources = behaviors;
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "You do not have permission to view anomalies."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Anomalies could not be loaded. The backend may be unreachable.";
  }

  const filtered = Boolean(
    q.log_source_id ||
      q.instance_id ||
      anomalyType ||
      subject.subject_kind ||
      q.state ||
      q.severity ||
      q.evidence_status ||
      q.since ||
      q.until,
  );
  const items = page?.items ?? [];
  // The first column names what the rows are about. A page of log-source rows
  // keeps its "Source" heading; once a WAF endpoint is on the page the column
  // holds subjects of two kinds, and the unit of the observed figures differs
  // per row, so each cell carries its own unit instead of the heading.
  const hasWaf = items.some(isWafRow);

  return (
    <>
      <PageHeader
        title="Anomalies"
        description="Open an anomaly to see what changed during its interval. Times are UTC."
      />

      <AnomalyFilters
        values={{
          subject_kind: subject.subject_kind,
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
            <table className="sticky-actions">
              <caption className="sr-only">
                Detected anomalies with lifecycle state, observed against
                expected volume, deviation, severity and evidence status.
              </caption>
              <thead>
                <tr>
                  <th scope="col">{hasWaf ? "Subject" : "Source"}</th>
                  <th scope="col">Detector</th>
                  <th scope="col">State</th>
                  <th scope="col" className="num">
                    {hasWaf ? "Observed → Expected" : "Observed → Expected (EPS)"}
                  </th>
                  <th scope="col" className="num">
                    Deviation
                  </th>
                  <th scope="col">Severity</th>
                  <th scope="col" className="num">
                    Started
                  </th>
                  <th scope="col">Evidence</th>
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
            }}
          />
        </>
      )}
    </>
  );
}
