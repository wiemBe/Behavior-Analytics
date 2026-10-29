// Option reads for chart.search_results. This file imports the API client only:
// the registry loads it into the shell, and the chart bundle must not come with it.

import { api, type MetricKeyList, type ScheduledSearch } from "@/lib/api";

import type { OptionsSource } from "./types";

const byLabel = (a: { label: string }, b: { label: string }) => a.label.localeCompare(b.label);

/** Scheduled searches (`GET /searches`). The layout stores the search id. */
export const SEARCH_OPTIONS: OptionsSource = {
  messageKey: "scheduledSearches",
  what: "scheduled searches",
  emptyReason: "No scheduled search exists yet, so there is nothing to chart.",
  read: () => ({
    key: "/searches",
    fetch: (signal) => api.searches(signal),
    options: (rows: ScheduledSearch[]) => ({
      options: rows.map((row) => ({ id: row.id, label: row.name })).sort(byLabel),
      total: rows.length,
    }),
  }),
};

/**
 * One search's stored metric keys (`GET /searches/{id}/metric-keys`).
 * A search that has never run has no key yet; `total` is still offered so the
 * analyst can place the widget, and the chart shows the empty state.
 */
export const SEARCH_METRIC_OPTIONS: OptionsSource = {
  messageKey: "searchMetricKeys",
  what: "metric keys",
  emptyReason: "This search has stored no metric key yet.",
  dependsOn: "search_id",
  needs: "Choose a search first",
  read: (searchId) => {
    if (searchId === null) return null;
    return {
      key: `/searches/${searchId}/metric-keys?limit=1000`,
      fetch: (signal) => api.searchMetricKeys(searchId, 1000, signal),
      options: (page: MetricKeyList) => {
        const options = page.keys
          .map((row) => ({ id: row.metric_key, label: row.metric_key }))
          .sort(byLabel);
        if (options.length === 0) return { options: [{ id: "total", label: "total" }], total: 0 };
        return { options, total: page.total };
      },
    };
  },
};
