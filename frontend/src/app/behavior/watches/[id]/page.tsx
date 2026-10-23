// One property watch: what values does this property take on this scope, and has anything about
// them changed?
//
// The first screen is the header, the series chart with the parsed share beneath it, and the
// values table; the Incidents, Threshold rules and Definition tabs follow.

import type { Metadata } from "next";
import Link from "next/link";

import { PwStatus } from "@/components/propertywatch/PwStatus";
import { PropertyWatchWriteNotice } from "@/components/propertywatch/PropertyWatchWriteNotice";
import { WatchActions } from "@/components/propertywatch/WatchActions";
import { WatchEditor } from "@/components/propertywatch/WatchEditor";
import type { AqlRead } from "@/components/propertywatch/WatchAql";
import { WatchWorkspace } from "@/components/propertywatch/WatchWorkspace";
import {
  propertyWatchAccessFrom,
  type PropertyWatchAccess,
} from "@/components/propertywatch/authoring";
import { propertiesText, relativeAge, scopeText } from "@/components/propertywatch/pwFormat";
import {
  defaultSelection,
  parsePwRange,
  parsePwSelection,
  parsePwSort,
  VALUES_PAGE_SIZE,
} from "@/components/propertywatch/pwRange";
import { customRuleAccessFrom } from "@/components/customrules/authoring";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  ApiError,
  api,
  type AnomalySummary,
  type PagedPwValues,
  type PropertyWatch,
  type PwValueSeries,
} from "@/lib/api";
import { historyHeaderText } from "@/components/propertywatch/history";
import { UtcTime } from "@/components/ui/UtcTime";

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
  const [catalog, scope, buckets, values, incidentPage, aql] = await Promise.all([
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
    // The generated query text. A refusal keeps its status so the tab can name the permission.
    api
      .propertyWatchAql(id)
      .then((a): AqlRead => ({ kind: "ok", aql: a }))
      .catch((err): AqlRead => ({ kind: "error", status: err instanceof ApiError ? err.status : 0 })),
  ]);
  const caller = await api.caller().catch(() => null);
  const access: PropertyWatchAccess = caller ? propertyWatchAccessFrom(caller) : "unknown";
  const ruleAccess = caller ? customRuleAccessFrom(caller) : ("unknown" as const);

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
        actions={access === "writer" ? <WatchActions watch={watch} catalog={catalog} /> : null}
        meta={
          <>
            <PwStatus status={watch.status} reason={watch.status_reason} history={watch.history} strong inline />
            {watch.status !== "READING_HISTORY" && (
              <>
                <span className="muted" aria-hidden="true">·</span>
                <span>{historyHeaderText(watch.history)}</span>
              </>
            )}
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
              Last {watch.history.state === "READING" ? "collected " : ""}bucket{" "}
              {last ? (
                <>
                  <UtcTime value={last} />{" "}
                  <span className="muted">({relativeAge(last, end)})</span>
                </>
              ) : (
                "none yet"
              )}
            </span>
          </>
        }
      />

      <section className="pw-definition-editor" aria-labelledby="pw-edit-title">
        <h2 id="pw-edit-title" className="sr-only">Watch definition editor</h2>
        {access === "writer" && catalog ? (
          <details className="pw-edit-disclosure">
            <summary>Edit watch</summary>
            <WatchEditor catalog={catalog} mode="edit" watch={watch} initialScope={scope} />
          </details>
        ) : access === "writer" ? (
          <div className="notice" role="alert">
            <p><strong>The editor bounds could not be loaded.</strong></p>
            <p>The watch remains readable. Reload the page to try the catalog read again.</p>
          </div>
        ) : (
          <PropertyWatchWriteNotice unknown={access === "unknown"} />
        )}
      </section>

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
        aql={aql}
        ruleAccess={ruleAccess}
        initialSelected={selection}
        initialSeries={initialSeries}
        windowStart={start.toISOString()}
        windowEnd={end.toISOString()}
      />
    </>
  );
}
