// The classification review list.
//
// The question: which sources are new or ambiguously classified, and what
// should their class be? Filters live in the URL; the table rows hold the two
// actions an analyst takes (set the class, mark reviewed).

import type { Metadata } from "next";

import { FleetSourceFilters } from "@/components/fleet/FleetSourceFilters";
import { FleetSourcesTable } from "@/components/fleet/FleetSourcesTable";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, fleetApi, type ClassificationRule, type FleetSource } from "@/lib/api";
import {
  activeFilterLabels,
  fleetApiParams,
  fleetQueryParams,
  isFleetFiltered,
  parseFleetSourceQuery,
} from "@/lib/fleet";

export const metadata: Metadata = {
  title: "Fleet sources",
};

export default async function FleetSourcesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = parseFleetSourceQuery(await searchParams);

  let items: FleetSource[] = [];
  let total = 0;
  let limit = 50;
  let error: string | null = null;
  let rules: ClassificationRule[] = [];
  try {
    // The rules only name the rule that matched; their failure must not blank
    // the list, so it is tolerated separately.
    const [page, ruleList] = await Promise.all([
      fleetApi.sources(fleetApiParams(query)),
      fleetApi.rules().catch(() => null),
    ]);
    items = page.items;
    total = page.total;
    limit = page.limit;
    rules = ruleList ?? [];
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "Your role cannot read the fleet (permission read:fleet). Nothing was changed."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "The source list could not be loaded. The backend may be unreachable; check that it is running, then reload the page.";
  }

  const filters = activeFilterLabels(query);

  return (
    <>
      <PageHeader
        title="Fleet sources"
        description="New and ambiguously classified log sources, with the class each one was given and why. Times are UTC."
      />
      <FleetSourceFilters query={query} />
      {filters.length > 0 && (
        <ul className="filter-chips" aria-label="Active filters">
          {filters.map((f) => (
            <li key={f} className="filter-chip">
              {f}
            </li>
          ))}
        </ul>
      )}

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>The source list could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="notice">
          <p>
            <strong>
              {isFleetFiltered(query)
                ? "No source matches these filters."
                : "No log sources are in the inventory."}
            </strong>
          </p>
          <p>
            {isFleetFiltered(query)
              ? "Widen or clear the filters to see more sources."
              : "Run an inventory sync from the Log Sources page; sources appear here once they are synced."}
          </p>
        </div>
      ) : (
        <>
          <FleetSourcesTable items={items} rules={rules} />
          <Pagination
            total={total}
            limit={limit}
            offset={query.offset}
            basePath="/fleet/sources"
            params={fleetQueryParams(query)}
          />
        </>
      )}
    </>
  );
}
