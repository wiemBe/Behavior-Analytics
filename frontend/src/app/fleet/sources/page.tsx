// The classification review list.
//
// The question: which sources are new or ambiguously classified, and what
// should their class be? Filters live in the URL; the table rows hold the two
// actions an analyst takes (set the class, mark reviewed).

import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";

import { FleetSourceFilters } from "@/components/fleet/FleetSourceFilters";
import { FleetSourcesTable } from "@/components/fleet/FleetSourcesTable";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { ApiError, fleetApi, type ClassificationRule, type FleetSource } from "@/lib/api";
import {
  activeFilterLabels,
  fleetApiParams,
  fleetQueryParams,
  isFleetFiltered,
  parseFleetSourceQuery,
} from "@/lib/fleet";
import { fleetTextOf } from "@/lib/fleetText";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("fleet.meta");
  return { title: t("sources") };
}

async function FleetSourcesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const query = parseFleetSourceQuery(await searchParams);
  const t = await getTranslations("fleet.sourcesPage");
  const text = fleetTextOf(await getTranslations(), await getLocale());

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
        ? text.say("fleet.errors.forbidden")
        : err instanceof ApiError && err.status === 401
          ? text.say("errors.sessionExpired")
          : t("unreachable");
  }

  const filters = activeFilterLabels(query, text);

  return (
    <>
      <PageHeader
        guide={guideFor("/fleet/sources")}
        title={t("title")}
        description={t("description")}
      />
      <FleetSourceFilters query={query} />
      {filters.length > 0 && (
        <ul className="filter-chips fleet-filter-chips" aria-label={t("activeFilters")}>
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
            <strong>{t("noticeHeading")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : items.length === 0 ? (
        <div className="notice">
          <p>
            <strong>
              {isFleetFiltered(query) ? t("emptyFiltered") : t("emptyInventory")}
            </strong>
          </p>
          <p>
            {isFleetFiltered(query) ? t("emptyFilteredHint") : t("emptyInventoryHint")}
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

export default withRouteMessages("/fleet/sources", FleetSourcesPage);
