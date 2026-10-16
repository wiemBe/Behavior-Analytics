"use client";

// chart.search_results (Phase F §F.9.2). One search's stored aggregates, drawn by the same
// ResultTrend the search page uses. The layout holds a search id and a metric key, both
// enumerated. The dashboard range filters the trend read, which is the only read that takes one.

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { ResultTrend, SEARCH_RESULTS_FORBIDDEN } from "@/components/ResultTrend";
import { ConfigNotice } from "@/components/catalog/ConfigNotice";
import { useApiResource } from "@/hooks/useApiResource";
import { ApiError, actionErrorMessage, api } from "@/lib/api";
import { RANGE_TEXT, useEffectiveRange, type RangedProps } from "@/widgets/runtime";

export interface SearchResultsConfig {
  search_id: string | null;
  metric_key: string | null;
}

const SERIES = new Set(["LINE", "BAR", "SINGLE_VALUE"]);

export function SearchResultsWidget({
  id,
  config,
  range,
}: { id: string; config: SearchResultsConfig } & RangedProps) {
  const window = useEffectiveRange(range);
  const searchId = typeof config.search_id === "string" && config.search_id !== "" ? config.search_id : null;
  const searchState = useApiResource(
    searchId ? `/searches/${searchId}` : null,
    (signal) => api.search(searchId as string, signal),
  );

  const loaded = searchState.status === "ok" ? searchState.data : null;
  const viz = (loaded?.visualization_type ?? "LINE").toUpperCase();
  const title = loaded?.name ?? "Search results";
  let body;
  if (searchId === null) {
    body = (
      <ConfigNotice
        lead="Choose a search."
        text="No search is set for this widget, so there is nothing to chart."
        hint="Open Customize, then Configure, to choose one."
      />
    );
  } else if (searchState.status === "loading" || searchState.status === "idle") {
    body = (
      <p className="tw-state" role="status">
        Loading result trend…
      </p>
    );
  } else if (searchState.status === "forbidden") {
    body = (
      <p className="tw-state" role="alert">
        {SEARCH_RESULTS_FORBIDDEN}
      </p>
    );
  } else if (searchState.status === "error" && searchState.httpStatus === 404) {
    body = (
      <ConfigNotice
        lead="This search was deleted."
        text="The search set for this widget no longer exists."
        hint="Open Customize, then Configure, to choose another."
      />
    );
  } else if (searchState.status === "error" || loaded === null) {
    const status = searchState.status === "error" ? searchState.httpStatus : null;
    body = (
      <p className="tw-state" role="alert">
        {actionErrorMessage(new ApiError(status ?? 500, ""))}
      </p>
    );
  } else {
    body = (
      <ResultTrend
        variant="widget"
        honourRange
        range={range}
        searchId={searchId}
        search={loaded}
        visualization={loaded.visualization_type}
        metricKey={config.metric_key ?? undefined}
      />
    );
  }

  return (
    <WidgetFrame
      id={`search-results-${id}`}
      title={title}
      what="search results"
      permission="read:searches"
      windowText={searchId !== null && SERIES.has(viz) ? RANGE_TEXT[window] : undefined}
      link={{ href: searchId ? `/searches/${searchId}` : "/searches", label: "Search" }}
      fillBody
      state={{ kind: "ok", data: null, asOf: null }}
    >
      {() => body}
    </WidgetFrame>
  );
}
