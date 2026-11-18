// The query model of the fleet source list (UIQ-7b).
//
// The URL is the whole description of the view: search, every facet, sort, page size and offset.
// A value arrives from a URL a third party may have written, so each one is checked against its
// allow-list before it reaches the API; what is not on the list is dropped, never forwarded.

import {
  type ClassificationBasis,
  type ClassificationReviewStatus,
  type FleetEpsState,
  type FleetFlow,
  type FleetListParams,
  type FleetProfileKind,
  type FleetSourceFacets,
  type FleetSourceSort,
  type SourceClass,
  type YesNo,
} from "@/lib/api";
import { ALL_CLASSES, BASIS_OPTIONS } from "@/lib/fleet";

export const FLOWS: FleetFlow[] = ["FLOWING", "SILENT", "DISABLED_IN_QRADAR", "NEVER"];
/** QRadar's own enabled flag, mapped onto the flow facet: DISABLED_IN_QRADAR is exactly
 * `enabled = false` (the API ranks it first), so its count is exact and the complement is the rest. */
export const DISABLED_FLOW: FleetFlow = "DISABLED_IN_QRADAR";
export const ENABLED_FLOWS: FleetFlow[] = ["FLOWING", "SILENT", "NEVER"];
export type QradarStatus = "ENABLED" | "DISABLED";

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v) => b.includes(v));
}

/** The QRadar status a flow filter amounts to, or null when it is anything else. */
export function qradarStatusOf(flow: readonly FleetFlow[]): QradarStatus | null {
  if (sameSet(flow, [DISABLED_FLOW])) return "DISABLED";
  if (sameSet(flow, ENABLED_FLOWS)) return "ENABLED";
  return null;
}

export function withQradarStatus(flow: readonly FleetFlow[], status: QradarStatus | null): FleetFlow[] {
  return status === "DISABLED" ? [DISABLED_FLOW] : status === "ENABLED" ? [...ENABLED_FLOWS] : [...flow];
}

export const CRITICALITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;
export const YES_NO: YesNo[] = ["YES", "NO"];
/** Longest owner name the API takes. */
export const OWNER_MAX = 255;

/** The optional columns, in table order. The compact default shows none of them. */
export const OPTIONAL_COLUMNS = [
  "description",
  "protocol",
  "collector",
  "credibility",
  "auto",
  "status",
  "created",
  "modified",
  "criticality",
  "owner",
  "monitoring",
  "maintenance",
] as const;
export type OptionalColumn = (typeof OPTIONAL_COLUMNS)[number];
/** Columns of numbers: right-aligned, tabular. */
export const NUMERIC_COLUMNS: readonly OptionalColumn[] = ["protocol", "collector", "credibility"];

export const EPS_STATES: FleetEpsState[] = [
  "BELOW_EXPECTED",
  "WITHIN_EXPECTED",
  "ABOVE_EXPECTED",
  "NO_BASELINE",
];
export const PROFILE_KINDS: FleetProfileKind[] = ["WAF", "IPS", "IDENTITY", "NONE"];
export const SORTS: FleetSourceSort[] = ["name", "type", "class", "eps", "last_event", "silent_for"];
export const PAGE_SIZES = [25, 50, 100, 200, 500] as const;
export const DEFAULT_PAGE_SIZE = 50;
/** The most sources one bulk request takes (the API refuses more). */
export const BULK_MAX = 500;
/** `api_max_export_rows`: the CSV holds at most this many rows. */
export const EXPORT_CAP = 10_000;
export const Q_MAX = 100;

export interface SourceListQuery {
  q: string;
  class: SourceClass[];
  type_id: number[];
  group: number[];
  criticality: string[];
  owner: string[];
  auto_discovered: YesNo[];
  monitoring: YesNo[];
  maintenance: YesNo[];
  collector: number[];
  cols: OptionalColumn[];
  flow: FleetFlow[];
  eps: FleetEpsState[];
  profile: FleetProfileKind[];
  review_status: ClassificationReviewStatus | "";
  has_conflict: "true" | "false" | "";
  basis: ClassificationBasis | "";
  sort: FleetSourceSort;
  order: "asc" | "desc";
  limit: number;
  offset: number;
}

export type RawQuery = Record<string, string | string[] | undefined>;

function all(raw: RawQuery, key: string): string[] {
  const v = raw[key];
  return v === undefined ? [] : Array.isArray(v) ? v : [v];
}

function pick<T extends string>(values: string[], allowed: readonly T[]): T[] {
  const kept = values.filter((v): v is T => (allowed as readonly string[]).includes(v));
  return [...new Set(kept)];
}

/** `cols=a,b`: the known columns, in table order, each once; anything else is dropped. */
export function columnsOf(value: string): OptionalColumn[] {
  const asked = new Set(value.split(","));
  return OPTIONAL_COLUMNS.filter((c) => asked.has(c));
}

export function parseSourceListQuery(raw: RawQuery): SourceListQuery {
  const one = (key: string) => all(raw, key)[0] ?? "";
  const limit = Number(one("limit"));
  const offset = Number(one("offset"));
  const review = one("review_status");
  const conflict = one("has_conflict");
  const basis = one("basis");
  const sort = one("sort");
  const ids = (key: string) => [
    ...new Set(
      all(raw, key)
        .filter((v) => /^\d{1,9}$/.test(v))
        .map(Number),
    ),
  ];
  return {
    q: one("q").trim().slice(0, Q_MAX),
    class: pick(all(raw, "class"), ALL_CLASSES),
    type_id: ids("type_id"),
    group: ids("group"),
    criticality: pick(all(raw, "criticality"), CRITICALITIES),
    owner: [...new Set(all(raw, "owner").map((v) => v.slice(0, OWNER_MAX)).filter(Boolean))],
    auto_discovered: pick(all(raw, "auto_discovered"), YES_NO),
    monitoring: pick(all(raw, "monitoring"), YES_NO),
    maintenance: pick(all(raw, "maintenance"), YES_NO),
    collector: ids("collector"),
    cols: columnsOf(one("cols")),
    flow: pick(all(raw, "flow"), FLOWS),
    eps: pick(all(raw, "eps"), EPS_STATES),
    profile: pick(all(raw, "profile"), PROFILE_KINDS),
    review_status: review === "NEW" || review === "REVIEWED" ? review : "",
    has_conflict: conflict === "true" || conflict === "false" ? conflict : "",
    basis: (BASIS_OPTIONS as string[]).includes(basis) ? (basis as ClassificationBasis) : "",
    sort: (SORTS as string[]).includes(sort) ? (sort as FleetSourceSort) : "name",
    order: one("order") === "desc" ? "desc" : "asc",
    limit: (PAGE_SIZES as readonly number[]).includes(limit) ? limit : DEFAULT_PAGE_SIZE,
    offset: Number.isInteger(offset) && offset > 0 ? offset : 0,
  };
}

/** Filters that narrow the list (everything but sort, page size and offset). */
export function filterCount(q: SourceListQuery): number {
  return (
    (q.q ? 1 : 0) +
    q.class.length +
    q.type_id.length +
    q.group.length +
    q.criticality.length +
    q.owner.length +
    q.auto_discovered.length +
    q.monitoring.length +
    q.maintenance.length +
    q.collector.length +
    q.flow.length +
    q.eps.length +
    q.profile.length +
    (q.review_status ? 1 : 0) +
    (q.has_conflict ? 1 : 0) +
    (q.basis ? 1 : 0)
  );
}

export function isFiltered(q: SourceListQuery): boolean {
  return filterCount(q) > 0;
}

export function toApiParams(q: SourceListQuery): FleetListParams {
  return {
    q: q.q || undefined,
    class: q.class.length ? q.class : undefined,
    type_id: q.type_id.length ? q.type_id : undefined,
    group: q.group.length ? q.group : undefined,
    criticality: q.criticality.length ? q.criticality : undefined,
    owner: q.owner.length ? q.owner : undefined,
    auto_discovered: q.auto_discovered.length ? q.auto_discovered : undefined,
    monitoring: q.monitoring.length ? q.monitoring : undefined,
    maintenance: q.maintenance.length ? q.maintenance : undefined,
    collector: q.collector.length ? q.collector : undefined,
    flow: q.flow.length ? q.flow : undefined,
    eps: q.eps.length ? q.eps : undefined,
    profile: q.profile.length ? q.profile : undefined,
    review_status: q.review_status || undefined,
    has_conflict: q.has_conflict ? q.has_conflict === "true" : undefined,
    basis: q.basis || undefined,
    sort: q.sort,
    order: q.order,
    limit: q.limit,
    offset: q.offset,
  };
}

/** The URL's query string for a view; defaults are left out so a clean URL stays clean. */
export function toSearch(q: SourceListQuery): string {
  const parts: string[] = [];
  const add = (k: string, v: string | number) => parts.push(`${k}=${encodeURIComponent(String(v))}`);
  if (q.q) add("q", q.q);
  q.class.forEach((v) => add("class", v));
  q.type_id.forEach((v) => add("type_id", v));
  q.group.forEach((v) => add("group", v));
  q.criticality.forEach((v) => add("criticality", v));
  q.owner.forEach((v) => add("owner", v));
  q.auto_discovered.forEach((v) => add("auto_discovered", v));
  q.monitoring.forEach((v) => add("monitoring", v));
  q.maintenance.forEach((v) => add("maintenance", v));
  q.collector.forEach((v) => add("collector", v));
  if (q.cols.length) add("cols", q.cols.join(","));
  q.flow.forEach((v) => add("flow", v));
  q.eps.forEach((v) => add("eps", v));
  q.profile.forEach((v) => add("profile", v));
  if (q.review_status) add("review_status", q.review_status);
  if (q.has_conflict) add("has_conflict", q.has_conflict);
  if (q.basis) add("basis", q.basis);
  if (q.sort !== "name" || q.order !== "asc") {
    add("sort", q.sort);
    add("order", q.order);
  }
  if (q.limit !== DEFAULT_PAGE_SIZE) add("limit", q.limit);
  if (q.offset > 0) add("offset", q.offset);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** A changed view starts on its first page. */
export function changed(q: SourceListQuery, patch: Partial<SourceListQuery>): SourceListQuery {
  return { ...q, ...patch, offset: patch.offset ?? 0 };
}

/** The click on a sortable header: a new column starts ascending, the same column flips. */
export function sortedBy(q: SourceListQuery, column: FleetSourceSort): SourceListQuery {
  const order = q.sort === column && q.order === "asc" ? "desc" : "asc";
  return changed(q, { sort: column, order });
}

export function clearedFilters(q: SourceListQuery): SourceListQuery {
  return {
    ...q,
    q: "",
    class: [],
    type_id: [],
    group: [],
    criticality: [],
    owner: [],
    auto_discovered: [],
    monitoring: [],
    maintenance: [],
    collector: [],
    flow: [],
    eps: [],
    profile: [],
    review_status: "",
    has_conflict: "",
    basis: "",
    offset: 0,
  };
}

export function toggled<T>(values: readonly T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

/** The name of a group id: from the facet when it is listed there, else the id itself. */
export function groupName(facets: FleetSourceFacets | undefined, id: number): string {
  return facets?.group.find((g) => g.group_id === id)?.name ?? `#${id}`;
}

/** The name of a type id: from the facet when it is listed there, else the id itself. */
export function typeName(facets: FleetSourceFacets | undefined, id: number): string {
  return facets?.type.find((t) => t.type_id === id)?.name ?? `#${id}`;
}


/** The permission that changes a source's class. */
export const WRITE_PERMISSION = "log_source:write";
export const ADMIN_PERMISSION = "admin:*";

/** The grant itself, the administrator wildcard, or a resolved capability. */
export function holds(
  me: { permissions: string[]; effective_permissions: string[] } | null,
  permission: string,
): boolean {
  if (!me) return false;
  return (
    me.permissions.includes(permission) ||
    me.permissions.includes("admin:*") ||
    me.effective_permissions.includes(permission)
  );
}

