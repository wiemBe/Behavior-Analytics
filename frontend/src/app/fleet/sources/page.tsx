// The fleet source list.
//
// The question: which of my sources are which, and which need me? The URL holds the whole view
// (search, facets, sort, page size, offset). This page reads the list for it; the browser
// component under it searches, filters, sorts, selects and navigates by rewriting the URL.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { FleetSourcesBrowser, type FleetSourcesFailure } from "@/components/fleet/FleetSourcesBrowser";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { ApiError, fleetApi, type FleetSourcePage } from "@/lib/api";
import { parseSourceListQuery, toApiParams, type RawQuery } from "@/lib/fleetSources";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("fleet.meta");
  return { title: t("sources") };
}

async function FleetSourcesPage({ searchParams }: { searchParams: Promise<RawQuery> }) {
  const query = parseSourceListQuery(await searchParams);
  const t = await getTranslations("fleet.sourcesPage");

  let page: FleetSourcePage | null = null;
  let failure: FleetSourcesFailure | null = null;
  try {
    page = await fleetApi.sourceList(toApiParams(query));
  } catch (err) {
    failure =
      err instanceof ApiError && err.status === 403
        ? "forbidden"
        : err instanceof ApiError && err.status === 401
          ? "session"
          : "unreachable";
  }

  return (
    <>
      <PageHeader guide={guideFor("/fleet/sources")} title={t("title")} />
      <FleetSourcesBrowser query={query} page={page} failure={failure} nowIso={new Date().toISOString()} />
    </>
  );
}

export default withRouteMessages("/fleet/sources", FleetSourcesPage);
