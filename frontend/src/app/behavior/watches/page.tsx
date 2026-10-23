// Property watches: which watches exist, and which need attention?
//
// One table, no KPI row. Watches that need an analyst (an open incident, a stale collection, a
// property that parses on nothing, a stale or oversized scope) sort first. The collection and
// alerting switches are said above the table, because a watch whose family is off is not being
// judged however the row reads.
//
// The New watch action and the editor are PW-8's: this page links to no route that does not
// exist. PW-8 adds its action to the `PageHeader` below.

import type { Metadata } from "next";
import Link from "next/link";

import { CadenceLatency, canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { PwStatus } from "@/components/propertywatch/PwStatus";
import {
  propertyWatchAccessFrom,
  type PropertyWatchAccess,
} from "@/components/propertywatch/authoring";
import {
  coverageText,
  pwLatencyFrom,
  propertiesText,
  relativeAge,
  scopeText,
  sortWatches,
} from "@/components/propertywatch/pwFormat";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, type PropertyWatch, type PropertyWatchCatalog } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

export const metadata: Metadata = {
  title: "Property watches",
};

export default async function PropertyWatchesPage() {
  let watches: PropertyWatch[] = [];
  let catalog: PropertyWatchCatalog | null = null;
  let forbidden = false;
  let failed = false;
  let access: PropertyWatchAccess = "unknown";
  let canLink = false;
  try {
    watches = await api.propertyWatches();
  } catch (error) {
    forbidden = error instanceof ApiError && error.status === 403;
    failed = !forbidden;
  }
  if (!failed && !forbidden) {
    // The switches only qualify the list: their failure must not blank it, and it is said.
    catalog = await api.propertyWatchCatalog().catch(() => null);
    const caller = await api.caller().catch(() => null);
    access = caller ? propertyWatchAccessFrom(caller) : "unknown";
    canLink = canReadSearchCadence(caller);
  }
  const now = new Date();
  const rows = sortWatches(watches);

  return (
    <>
      <PageHeader
        title="Property watches"
        actions={
          access === "writer" ? (
            <Link className="action" href="/behavior/watches/new">New watch</Link>
          ) : null
        }
      />

      {forbidden ? (
        <div className="notice" role="alert">
          <p>
            <strong>Property watches are forbidden for this account.</strong>
          </p>
          <p>
            Reading this route needs <code>read:property_watches</code>. Ask an admin to grant a
            role that includes it. Nothing was changed.
          </p>
        </div>
      ) : failed ? (
        <div className="notice" role="alert">
          <p>
            <strong>Property watches could not be loaded.</strong>
          </p>
          <p>
            Nothing is shown, which is not the same as there being none. The backend may be
            unreachable: check that it is running, then reload the page. No watch was changed.
          </p>
        </div>
      ) : (
        <>
          <div className="pw-notices">
            {catalog === null ? (
              <div className="banner warn" role="status">
                <span>
                  <strong>SWITCHES NOT READ.</strong> The collection and alerting switches could
                  not be loaded, so this page cannot say whether watches are being collected.
                </span>
              </div>
            ) : (
              <>
                {!catalog.collection_enabled && (
                  <div className="banner warn" role="status">
                    <span>
                      <strong>COLLECTION OFF.</strong> <code>PW_COLLECTION_ENABLED</code> is false,
                      so no search is sent and no watch is being judged, whatever a row says.
                    </span>
                  </div>
                )}
                {catalog.alerting_mode === "shadow" && (
                  <div className="banner" role="status">
                    <span>
                      <strong>SHADOW MODE.</strong> Incidents are recorded and nothing pages until{" "}
                      <code>PW_ALERTING_ENABLED</code> is on.
                    </span>
                  </div>
                )}
              </>
            )}
            <CadenceLatency className="pw-latency" parts={pwLatencyFrom(catalog?.cadence)} canLink={canLink} />
            {access !== "writer" && (
              <p className="pw-write-note" role={access === "unknown" ? "alert" : undefined}>
                {access === "unknown" ? "Permissions could not be read. " : ""}
                Creating and editing a watch needs <code>property_watch:write</code>.
              </p>
            )}
          </div>

          {rows.length === 0 ? (
            <div className="notice" role="status">
              <p>
                <strong>No property watch exists yet.</strong>
              </p>
              <p>
                A property watch counts the values of one or two properties, such as a username,
                over a log source, a log source type or a group, in 5-minute buckets. It learns what
                is normal for each value and reports a value that appears, disappears or changes.
                Until one exists there is nothing to judge here.
              </p>
            </div>
          ) : (
            <TableScroll label="Property watches">
              <table className="table-dense pw-list" aria-label="Property watches">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Status and reason</th>
                    <th scope="col">Scope</th>
                    <th scope="col">Properties</th>
                    <th scope="col" className="num">
                      Coverage, last bucket
                    </th>
                    <th scope="col" className="num">
                      Open incidents
                    </th>
                    <th scope="col" className="num">
                      Last bucket (UTC)
                    </th>
                    <th scope="col">Enabled</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((w) => (
                    <tr key={w.id}>
                      <td className="cell-name">
                        <Link href={`/behavior/watches/${w.id}`}>{w.name}</Link>
                      </td>
                      <td className="pw-status-cell">
                        <PwStatus status={w.status} reason={w.status_reason} history={w.history} />
                      </td>
                      <td className="pw-scope-cell">{scopeText(w)}</td>
                      <td className="pw-props-cell">{propertiesText(w)}</td>
                      <td className="num">{coverageText(w.coverage)}</td>
                      <td className="num">{w.open_incidents}</td>
                      <td className="num cell-when nowrap">
                        {w.collected_through ? (
                          <>
                            <UtcTime value={w.collected_through} />
                            <span className="cell-sub">{relativeAge(w.collected_through, now)}</span>
                          </>
                        ) : (
                          <span className="muted">no bucket yet</span>
                        )}
                      </td>
                      <td>{w.enabled ? "Yes" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </>
      )}
    </>
  );
}
