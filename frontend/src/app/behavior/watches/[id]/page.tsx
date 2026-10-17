// One property watch: what values does this property take on this scope, and has anything about
// them changed?
//
// Read only. The first screen is the header, the series chart with the parsed share beneath it,
// and the values table; the Incidents and Definition tabs follow. The Threshold rules tab needs
// PW-6 and the Edit disclosure needs PW-8: neither is drawn until it exists.

import type { Metadata } from "next";
import Link from "next/link";

import { PwStatus } from "@/components/propertywatch/PwStatus";
import { WatchWorkspace } from "@/components/propertywatch/WatchWorkspace";
import { propertiesText, relativeAge, scopeText } from "@/components/propertywatch/pwFormat";
import {
  defaultSelection,
  parsePwRange,
  parsePwSelection,
  parsePwSort,
  VALUES_PAGE_SIZE,
} from "@/components/propertywatch/pwRange";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  ApiError,
  api,
  type AnomalySummary,
  type PagedPwValues,
  type PropertyWatch,
  type PwValueSeries,
} from "@/lib/api";
import { formatDateTime } from "@/lib/health";

import "@/components/ui/controls.css";
import "../watches.css";

export const metadata: Metadata = {
  title: "Property watch",
};

const INCIDENT_DAYS = 30;

function Problem({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <PageHeader title="Property watch" />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{children}</p>
      </div>
    </>
  );
}

export default async function PropertyWatchPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const range = parsePwRange(first(query.range));
  const sort = parsePwSort(first(query.sort));
  const parsedOffset = Number(first(query.offset) ?? 0);
  const offset = Number.isFinite(parsedOffset) && parsedOffset > 0 ? Math.floor(parsedOffset) : 0;
  const asked = parsePwSelection(query.value);

  let watch: PropertyWatch;
  try {
    watch = await api.propertyWatch(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return (
        <Problem title="This property watch does not exist.">
          It may have been deleted, or the link may be wrong.{" "}
          <Link href="/behavior/watches">Open property watches</Link> to see the ones that exist.
        </Problem>
      );
    }
    if (err instanceof ApiError && err.status === 403) {
      return (
        <Problem title="This property watch is forbidden for this account.">
          Reading it needs <code>read:property_watches</code>. Ask an admin to grant a role that
          includes it. Nothing was changed.
        </Problem>
      );
    }
    if (err instanceof ApiError && err.status === 401) {
      return (
        <Problem title="Your session has expired.">Sign in again to continue. Nothing was changed.</Problem>
      );
    }
    return (
      <Problem title="This property watch could not be loaded.">
        The backend may be unreachable: check that it is running, then reload the page. Nothing was
        changed.
      </Problem>
    );
  }

  const end = new Date();
  const start = new Date(end.getTime() - range.hours * 3600_000);
  const since = new Date(end.getTime() - INCIDENT_DAYS * 86_400_000);
  // Each read below qualifies the page and none may blank it. A failure is `null`, which the
  // components word as "could not be loaded", never as "none".
  const [catalog, scope, buckets, values, incidentPage] = await Promise.all([
    api.propertyWatchCatalog().catch(() => null),
    api.propertyWatchScope(id).catch(() => null),
    api.propertyWatchBuckets(id, range.hours).catch(() => null),
    api
      .propertyWatchValues(id, { hours: range.hours, sort, limit: VALUES_PAGE_SIZE, offset })
      .catch(() => null as PagedPwValues | null),
    api
      .anomalies({
        subject_kind: "PW_VALUE",
        property_watch_id: id,
        since: since.toISOString(),
        until: end.toISOString(),
        limit: 50,
      })
      .catch(() => null),
  ]);

  const selection = defaultSelection(values?.items ?? [], asked);
  let initialSeries: PwValueSeries[] = [];
  if (selection.length > 0) {
    initialSeries = await api
      .propertyWatchSeries(id, selection, range.hours)
      .then((r) => r.series)
      .catch(() => [] as PwValueSeries[]);
  }
  const incidents: AnomalySummary[] | null = incidentPage ? incidentPage.items : null;
  const last = watch.collected_through;

  return (
    <>
      <PageHeader
        title={watch.name}
        meta={
          <>
            <PwStatus status={watch.status} reason={watch.status_reason} strong inline />
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span>{scopeText(watch)}</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span className="pw-prop-name">{propertiesText(watch)}</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span>5-minute buckets</span>
            <span className="muted" aria-hidden="true">
              ·
            </span>
            <span>
              Last bucket{" "}
              {last ? (
                <>
                  <time dateTime={last}>{formatDateTime(last)}</time>{" "}
                  <span className="muted">({relativeAge(last, end)})</span>
                </>
              ) : (
                "none yet"
              )}
            </span>
          </>
        }
      />

      <WatchWorkspace
        watch={watch}
        catalog={catalog}
        hours={range.hours}
        rangeKey={range.key}
        sort={sort}
        offset={offset}
        buckets={buckets}
        values={values}
        incidents={incidents}
        incidentsTotal={incidentPage ? incidentPage.total : null}
        scope={scope}
        initialSelected={selection}
        initialSeries={initialSeries}
        windowStart={start.toISOString()}
        windowEnd={end.toISOString()}
      />
    </>
  );
}
