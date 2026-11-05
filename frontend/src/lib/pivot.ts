// The pivot block's wire shape and its loader.
//
// The block is text for the analyst to take to the QRadar console: AQL, a console link and
// the UTC window. The backend builds all of it from stored values; nothing here assembles
// AQL, and the browser never sends any.

import { ApiError, api } from "@/lib/api";

export interface Pivot {
  kind: string;
  aql: string;
  /** The link "Open in QRadar" follows. Null when the console address is unknown. */
  console_url: string | null;
  /** The Log Activity AQL search. Differs from `console_url` only on an offense. */
  aql_search_url: string | null;
  console_unavailable_reason: string | null;
  time_range: { start: string; stop: string };
  notes: string[];
}

/** What a page hands the block: the pivot, or `failed` when it could not be read. */
export interface PivotLoad {
  pivot: Pivot | null;
  failed: boolean;
}

/**
 * A pivot read that never takes its page down. A 404 is "this object has no pivot" and
 * yields null, so the page omits the block; any other failure yields `failed`, and the block
 * says so instead of vanishing.
 */
export async function loadPivot(read: () => Promise<Pivot>): Promise<PivotLoad | null> {
  try {
    return { pivot: await read(), failed: false };
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    return { pivot: null, failed: true };
  }
}

export const pivotReads = {
  anomaly: (id: string) => loadPivot(() => api.anomalyPivot(id)),
  alert: (id: string) => loadPivot(() => api.alertPivot(id)),
  offense: (id: number) => loadPivot(() => api.offensePivot(id)),
  logSource: (id: string) => loadPivot(() => api.logSourcePivot(id)),
};
