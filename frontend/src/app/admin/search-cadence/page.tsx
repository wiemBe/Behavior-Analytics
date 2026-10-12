// How often each search family queries QRadar, and what that costs in
// detection delay. One table, one row per family, in the API's order.

import type { Metadata } from "next";

import { SearchCadenceTable } from "@/components/cadence/SearchCadenceTable";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type SearchCadenceItem } from "@/lib/api";

export const metadata: Metadata = {
  title: "Search cadence",
};

export default async function SearchCadencePage() {
  let items: SearchCadenceItem[] = [];
  let error: string | null = null;
  try {
    items = await api.searchCadence();
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 403
        ? "Your role cannot read search cadence (permission read:search_cadence). Nothing was changed."
        : err instanceof ApiError && err.status === 401
          ? "Your session has expired. Sign in again to continue."
          : "Search cadence could not be loaded. The backend may be unreachable; check that it is running, then reload the page. Nothing was changed.";
  }

  return (
    <>
      <PageHeader
        title="Search cadence"
        description="How often each search family queries QRadar, and the detection delay that costs. Times are UTC."
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>Search cadence could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="notice" role="alert">
          <p>
            <strong>No search families were returned.</strong>
          </p>
          <p>
            Four families (Phase A volume, WAF collection, anomaly explanations and rule matches)
            are seeded by the cadence migration, so an empty list means the seed did not run.
            Check that the database migrations have been applied, then reload the page.
          </p>
        </div>
      ) : (
        <SearchCadenceTable items={items} />
      )}
    </>
  );
}
