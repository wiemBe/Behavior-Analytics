"use client";

// The fleet source list as one unit: controls, table, paging and the states between them.
//
// The server page reads the list for the URL's query and hands the result down. Every control
// builds the next query and navigates; the list is never filtered in the browser. While a
// navigation is in flight the old rows stay and the region is marked busy.

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import type { TextTranslator } from "@/i18n/textTranslator";
import { useFleetText } from "@/lib/fleetText";
import { useCallback, useTransition } from "react";

import { Pagination } from "@/components/Pagination";
import type { FleetSourcePage } from "@/lib/api";
import { toSearch, clearedFilters, isFiltered, type SourceListQuery } from "@/lib/fleetSources";

import { FleetSourceControls } from "./FleetSourceControls";
import { FleetSourceList } from "./FleetSourceList";
import { FleetSyncNow } from "./FleetSyncNow";

import "./fleet-sources.css";

export type FleetSourcesFailure = "unreachable" | "forbidden" | "session";

export function FleetSourcesBrowser({
  query,
  page,
  failure,
  nowIso,
}: {
  query: SourceListQuery;
  page: FleetSourcePage | null;
  failure: FleetSourcesFailure | null;
  /** The server's clock, so an age is the same on the server and in the browser. */
  nowIso: string;
}) {
  const t = useTranslations("fleet.sourceList");
  const text = useFleetText();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const go = useCallback(
    (next: SourceListQuery, mode: "push" | "replace" = "push") => {
      startTransition(() => {
        const href = `/fleet/sources${toSearch(next)}`;
        if (mode === "replace") router.replace(href);
        else router.push(href);
      });
    },
    [router],
  );
  const refresh = useCallback(() => startTransition(() => router.refresh()), [router]);

  const rows = page?.items ?? [];
  const total = page?.total ?? 0;
  const filtered = isFiltered(query);

  return (
    <div className="fs-browser" aria-busy={pending}>
      <div className="fs-top">
        <FleetSourceControls query={query} facets={page?.facets} total={total} go={go} />
        <FleetSyncNow onFinished={refresh} />
      </div>
      {pending && (
        <p className="muted" role="status">
          {t("state.updating")}
        </p>
      )}
      {failure ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("state.errorHeading")}</strong>
          </p>
          <p>
            {failure === "forbidden"
              ? t("state.errorForbidden", { permission: "read:fleet" })
              : failure === "session"
                ? t("state.errorSession")
                : t("state.errorUnreachable")}
          </p>
          {failure === "unreachable" && (
            <button type="button" className="action secondary" onClick={refresh}>
              {t("state.retry")}
            </button>
          )}
        </div>
      ) : rows.length === 0 ? (
        <div className="notice">
          <p>
            <strong>{filtered ? t("state.emptyFiltered") : t("state.emptyInventory")}</strong>
          </p>
          <p>{filtered ? t("state.emptyFilteredHint", { filters: filterWords(query, text.say) }) : t("state.emptyInventoryHint")}</p>
          {filtered && (
            <button type="button" className="action secondary" onClick={() => go(clearedFilters(query))}>
              {t("state.clearAll")}
            </button>
          )}
        </div>
      ) : (
        <>
          <FleetSourceList rows={rows} query={query} nowIso={nowIso} go={go} onChanged={refresh} />
          <Pagination
            total={total}
            limit={page?.limit ?? query.limit}
            offset={query.offset}
            basePath="/fleet/sources"
            params={pagingParams(query)}
          />
        </>
      )}
    </div>
  );
}

/** The filter groups in words for the empty state: which fields were set, not their values. */
function filterWords(q: SourceListQuery, say: TextTranslator): string {
  const names: string[] = [];
  if (q.q) names.push(say("fleet.sourceList.chips.searchName"));
  if (q.class.length) names.push(say("fleet.sourceList.facets.class"));
  if (q.type_id.length) names.push(say("fleet.sourceList.facets.type"));
  if (q.flow.length) names.push(say("fleet.sourceList.facets.flow"));
  if (q.eps.length) names.push(say("fleet.sourceList.facets.eps"));
  if (q.profile.length) names.push(say("fleet.sourceList.facets.profile"));
  if (q.review_status) names.push(say("fleet.sourceList.chips.reviewName"));
  if (q.has_conflict) names.push(say("fleet.sourceList.chips.conflictName"));
  if (q.basis) names.push(say("fleet.sourceList.chips.basisName"));
  return names.join(", ");
}

/** What the paging links carry besides the offset: every filter, the sort and the page size. */
function pagingParams(q: SourceListQuery): Record<string, string | string[] | undefined> {
  const search = new URLSearchParams(toSearch({ ...q, offset: 0 }).slice(1));
  const out: Record<string, string[]> = {};
  for (const [k, v] of search) (out[k] ??= []).push(v);
  return out;
}
