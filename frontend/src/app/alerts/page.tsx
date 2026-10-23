import Link from "next/link";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type Alert, type Page } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { loadErrorMessage } from "@/lib/loadError";
import { UtcTime } from "@/components/ui/UtcTime";

import "./alerts.css";

const PAGE_SIZE = 100;
const ALERT_STATUSES = ["OPEN", "ACKNOWLEDGED", "RESOLVED"] as const;

function statusTone(status: string): string {
  if (status === "OPEN") return "crit";
  if (status === "ACKNOWLEDGED") return "warn";
  return "ok";
}

function alertStatus(value: string | undefined): string {
  return ALERT_STATUSES.includes(value as (typeof ALERT_STATUSES)[number]) ? value! : "";
}

function emptyMessage(status: string): string {
  if (status === "OPEN") return "No open alerts.";
  if (status === "ACKNOWLEDGED") return "No acknowledged alerts.";
  if (status === "RESOLVED") return "No resolved alerts.";
  return "No alerts have been raised.";
}

export default async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const status = alertStatus(params.status);
  let page: Page<Alert> | null = null;
  let error: string | null = null;
  try {
    page = await api.alerts({
      status: status || undefined,
      limit: PAGE_SIZE,
      offset,
    });
  } catch (err) {
    error = loadErrorMessage(err, "alerts", "read:alerts");
  }
  const alerts = page?.items ?? [];

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Lifecycle OPEN → ACKNOWLEDGED → RESOLVED. Repeated detections update the open alert and bump its occurrence count rather than creating duplicates. Times are UTC."
      />

      <form className="filters" method="get" action="/alerts">
        <label className="sr-only" htmlFor="alert-status">
          Alert status
        </label>
        <select id="alert-status" name="status" defaultValue={status}>
          <option value="">All statuses</option>
          <option value="OPEN">Open</option>
          <option value="ACKNOWLEDGED">Acknowledged</option>
          <option value="RESOLVED">Resolved</option>
        </select>
        <button type="submit">Filter</button>
        {status && <Link href="/alerts">Clear</Link>}
      </form>

      {error ? (
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      ) : alerts.length === 0 ? (
        <div className="notice">{emptyMessage(status)}</div>
      ) : (
        <>
          {page && page.total > alerts.length && (
            <p className="subtitle">
              Showing {formatCount(alerts.length)} of {formatCount(page.total)} alerts.
            </p>
          )}
          <TableScroll label="Alerts">
            <table className="table-dense" aria-label="Alerts">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th className="num">Occurrences</th>
                  <th>First seen</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td className="alert-title">
                      <Link href={`/alerts/${a.id}`}>{a.title}</Link>
                    </td>
                    <td><span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span></td>
                    <td><span className={`pill ${statusTone(a.status)}`}>{a.status}</span></td>
                    <td className="num">{formatCount(a.occurrence_count)}</td>
                    <td className="nowrap">{<UtcTime value={a.first_seen_at} variant="compact" />}</td>
                    <td className="nowrap">{<UtcTime value={a.last_seen_at} variant="compact" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/alerts"
            params={{ status }}
          />
        </>
      )}
    </>
  );
}
