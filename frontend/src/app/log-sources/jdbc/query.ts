// The address of the JDBC list: only filters the API accepts, in a fixed order.
// Mirrors `JdbcRepository.bounds`: an unknown/invalid value is dropped here so the
// browser never sends a request the API would answer 422, and every active filter
// survives a pagination link. The enum arrays are single-sourced in `@/lib/api`.

import { JDBC_FLOW_STATES, JDBC_PROTOCOL_IDS, type JdbcSourceListParams } from "@/lib/api";

/** Rows per page; the API's default is 100 and its maximum `api_max_page_size` defaults to 200. */
export const JDBC_PAGE_SIZE = 100;
export const JDBC_MAX_LIMIT = 200;
export const JDBC_Q_MAX = 200;

export type RawQuery = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

/** A decimal integer, sign included, or undefined; unsafe values are dropped (F6). */
function signedInteger(value: string): number | undefined {
  const text = value.trim();
  if (!/^-?\d+$/.test(text)) return undefined;
  const parsed = Number(text);
  return Number.isSafeInteger(parsed) ? parsed : undefined;
}

/** A non-negative decimal integer; "1.5", "-1" and unsafe values are not offsets or limits. */
function integer(value: string): number | undefined {
  const parsed = signedInteger(value);
  return parsed !== undefined && parsed >= 0 ? parsed : undefined;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function parseJdbcQuery(raw: RawQuery): JdbcSourceListParams {
  const limit = signedInteger(first(raw.limit));
  const offset = integer(first(raw.offset));
  const q = first(raw.q).trim().slice(0, JDBC_Q_MAX);
  const instanceId = first(raw.instance_id).trim();
  const protocol = integer(first(raw.protocol));
  const enabled = first(raw.enabled).trim().toLowerCase();
  const flowState = first(raw.flow_state).trim();
  const includeAbsent = first(raw.include_absent).trim().toLowerCase() === "true";
  const params: JdbcSourceListParams = {
    // The backend does `max(1, min(limit, api_max_page_size))`, so a negative integer clamps to 1.
    limit: Math.max(1, Math.min(limit ?? JDBC_PAGE_SIZE, JDBC_MAX_LIMIT)),
    offset: offset ?? 0,
  };
  if (q) params.q = q;
  if (UUID.test(instanceId)) params.instance_id = instanceId;
  if (protocol !== undefined && (JDBC_PROTOCOL_IDS as readonly number[]).includes(protocol)) {
    params.protocol = protocol as JdbcSourceListParams["protocol"];
  }
  if (enabled === "true" || enabled === "false") params.enabled = enabled === "true";
  if ((JDBC_FLOW_STATES as readonly string[]).includes(flowState)) {
    params.flow_state = flowState as JdbcSourceListParams["flow_state"];
  }
  // Only `true` is sent. Filtering on the absent state is meaningless while absent sources are
  // omitted (the API answers an empty page), so that filter implies the toggle.
  if (includeAbsent || params.flow_state === "ABSENT_FROM_INVENTORY") params.include_absent = true;
  return params;
}

/** Rebuild the address from parsed params: offset and every active filter survive. */
export function buildJdbcQuery(params: JdbcSourceListParams): string {
  const parts: string[] = [];
  const add = (key: string, value: string | number | boolean | undefined) => {
    if (value !== undefined) parts.push(`${key}=${encodeURIComponent(String(value))}`);
  };
  add("q", params.q);
  add("instance_id", params.instance_id);
  add("protocol", params.protocol);
  add("enabled", params.enabled);
  add("flow_state", params.flow_state);
  add("include_absent", params.include_absent ? true : undefined);
  add("limit", params.limit);
  add("offset", params.offset);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** "Clear filters" keeps the page size and returns to the first page (no offset). */
export function clearJdbcFilters(params: JdbcSourceListParams): JdbcSourceListParams {
  return { limit: params.limit ?? JDBC_PAGE_SIZE };
}
