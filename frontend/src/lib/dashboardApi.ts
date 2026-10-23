// The saved-dashboards client (UR.5.5). Types mirror `backend/app/schemas/dashboard.py`;
// the requests go through the same `request` / `ApiError` as every other read, so an error
// keeps the server's `detail` text ("you already have a dashboard named 'X'", "the limit is
// 20 dashboards per owner") for the UI to show as it is.

import type { DashboardLayout, LayoutWidget, WidgetRange } from "@/widgets/types";

import { ApiError, request } from "./api";

/** `PRIVATE` is the owner's alone; `SHARED` is read-only to every dashboard reader. */
export type DashboardVisibility = "PRIVATE" | "SHARED";

export interface DashboardListItem {
  id: string;
  name: string;
  owner_subject: string;
  visibility: DashboardVisibility;
  template_key: string | null;
  updated_at: string;
  widget_count: number;
  is_default: boolean;
}

export interface PagedDashboards {
  items: DashboardListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface DashboardOut {
  id: string;
  name: string;
  description: string | null;
  owner_subject: string;
  visibility: DashboardVisibility;
  template_key: string | null;
  template_version: number | null;
  schema_version: number;
  /** Opaque to the server: read it with `readLayout`, never trust its shape. */
  layout: unknown;
  revision: number;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
  is_default: boolean;
}

export interface DefaultDashboardOut {
  dashboard_id: string | null;
}

export interface DashboardCreate {
  name: string;
  description?: string | null;
  layout: DashboardLayout;
  template_key?: string | null;
  template_version?: number | null;
}

export interface DashboardPatch {
  name?: string;
  description?: string | null;
  visibility?: DashboardVisibility;
}

export interface DashboardReplace {
  name: string;
  description?: string | null;
  layout: DashboardLayout;
  expected_revision: number;
}

/** What a `PUT` answers: the saved dashboard, or a stale write (the server's `409`). */
export type ReplaceResult =
  | { kind: "saved"; dashboard: DashboardOut }
  | { kind: "conflict"; message: string; current_revision: number };

/** One more than the 20 a person may own, plus every shared one: the list is read whole. */
const LIST_LIMIT = 200;

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

export const listDashboards = (signal?: AbortSignal) =>
  request<PagedDashboards>(`/dashboards?limit=${LIST_LIMIT}`, { signal });

export const getDashboard = (id: string, signal?: AbortSignal) =>
  request<DashboardOut>(`/dashboards/${id}`, { signal });

export const createDashboard = (body: DashboardCreate) =>
  request<DashboardOut>("/dashboards", { method: "POST", ...json(body) });

export const patchDashboard = (id: string, body: DashboardPatch) =>
  request<DashboardOut>(`/dashboards/${id}`, { method: "PATCH", ...json(body) });

export const deleteDashboard = (id: string) =>
  request<void>(`/dashboards/${id}`, { method: "DELETE" });

export const duplicateDashboard = (id: string) =>
  request<DashboardOut>(`/dashboards/${id}/duplicate`, { method: "POST" });

/** `null` clears the default. */
export const setDefaultDashboard = (id: string | null) =>
  request<DefaultDashboardOut>("/dashboards/default", {
    method: "PUT",
    ...json({ dashboard_id: id }),
  });

/**
 * `PUT`. A `409` whose `detail` is an object is a stale write and comes back as a result;
 * a `409` whose `detail` is a string (a duplicate name) is a refusal and throws, as does
 * every other error.
 */
export async function replaceDashboard(
  id: string,
  body: DashboardReplace,
): Promise<ReplaceResult> {
  try {
    const dashboard = await request<DashboardOut>(`/dashboards/${id}`, {
      method: "PUT",
      ...json(body),
    });
    return { kind: "saved", dashboard };
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      const detail = error.detail;
      if (detail !== null && typeof detail === "object") {
        const { message, current_revision } = detail as Record<string, unknown>;
        if (typeof current_revision === "number") {
          return {
            kind: "conflict",
            message: typeof message === "string" ? message : error.message,
            current_revision,
          };
        }
      }
    }
    throw error;
  }
}

/** The same reads in the shape `useApiResource` takes: a key (the request's identity). */
export const dashboardReads = {
  list: () => ({
    key: `/dashboards?limit=${LIST_LIMIT}`,
    fetch: (signal: AbortSignal) => listDashboards(signal),
  }),
  one: (id: string) => ({
    key: `/dashboards/${id}`,
    fetch: (signal: AbortSignal) => getDashboard(id, signal),
  }),
};

export type LayoutRead =
  | { ok: true; layout: DashboardLayout }
  | { ok: false; reason: string; version: number | null };

const RANGES: readonly string[] = ["1h", "24h", "7d"];
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/**
 * A stored layout, as this client can show it. A version it does not know is not guessed at;
 * a widget `type` it does not know is kept (the registry renders it as unsupported).
 */
export function readLayout(raw: unknown): LayoutRead {
  if (!isObject(raw)) return { ok: false, reason: "The layout is not an object.", version: null };
  const version = typeof raw.v === "number" ? raw.v : null;
  if (raw.v !== 1) {
    return {
      ok: false,
      reason:
        version === null
          ? "The layout names no version."
          : `The layout is version ${version}; this client reads version 1.`,
      version,
    };
  }
  if (typeof raw.range !== "string" || !RANGES.includes(raw.range) || !Array.isArray(raw.widgets)) {
    return { ok: false, reason: "The layout is malformed.", version: 1 };
  }
  const widgets: LayoutWidget[] = [];
  for (const item of raw.widgets) {
    if (
      !isObject(item) ||
      typeof item.id !== "string" ||
      typeof item.type !== "string" ||
      ![item.x, item.y, item.w, item.h].every((n) => typeof n === "number" && Number.isFinite(n))
    ) {
      return { ok: false, reason: "The layout holds a malformed widget.", version: 1 };
    }
    widgets.push({
      id: item.id,
      type: item.type,
      x: item.x as number,
      y: item.y as number,
      w: item.w as number,
      h: item.h as number,
      range:
        typeof item.range === "string" && RANGES.includes(item.range)
          ? (item.range as WidgetRange)
          : null,
      config: isObject(item.config) ? item.config : {},
    });
  }
  return { ok: true, layout: { v: 1, range: raw.range as WidgetRange, widgets } };
}

/**
 * What the analyst reads when a dashboard request fails: the server's own `detail` when it sent
 * one (a duplicate name, the 20-dashboard limit, a validation message), and otherwise what is
 * known. An exception's message is never shown.
 */
export function describeRefusal(error: unknown): string {
  return asSentence(refusalWords(error));
}

/** The server's words as a sentence: first character capitalised, a full stop if none ends it. */
export function asSentence(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;
  const head = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(head) ? head : `${head}.`;
}

function refusalWords(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "The request did not reach the server. Check that the backend is reachable and try again.";
  }
  const detail = error.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) =>
        isObject(entry) && typeof entry.msg === "string"
          ? entry.msg.replace(/^Value error, /, "")
          : null,
      )
      .filter((text): text is string => text !== null);
    if (messages.length > 0) return messages.join("; ");
  }
  if (isObject(detail) && typeof detail.message === "string") return detail.message;
  return `The server answered HTTP ${error.status}.`;
}
