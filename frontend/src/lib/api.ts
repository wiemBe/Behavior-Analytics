// Thin typed client for the qradar-observability backend.
//
// All requests go through the backend API. The browser holds no QRadar
// credentials and never contacts QRadar or the MCP service directly.

import type { TextTranslator } from "@/i18n/textTranslator";
import { missingPermission } from "@/lib/refusal";

const PUBLIC_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

function baseUrl(): string {
  // Server Components execute inside the frontend container, where localhost
  // points back to Next.js. Browser-side actions still use the published host
  // URL because Docker service names are not resolvable by the browser.
  if (typeof window === "undefined") {
    return process.env.INTERNAL_API_BASE_URL ?? PUBLIC_BASE;
  }
  return PUBLIC_BASE;
}

export class ApiError extends Error {
  /**
   * `detail` is the response's `detail` exactly as sent. Most errors carry a
   * string, which is also `message`; a conflict may carry an object naming the
   * current version, which `message` cannot hold.
   */
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
    /** The whole JSON body of the failed response, when it had one: a conflict may carry state. */
    public body?: unknown,
  ) {
    super(message);
  }

  /**
   * The stable codes of a capacity refusal (T-145), read from the body: `ENABLED_CAP`,
   * `GROUP_TOO_LARGE`, `WATCH_SEARCH_LIMIT`, `TICK_BUDGET` or `HISTORY_BUDGET`. Empty when
   * the body carries none — a server that sends no code must not be guessed at from the
   * wording.
   */
  get codes(): string[] {
    return capacityCodes(this.body);
  }
}

/**
 * The codes a failure body carries, or none. A body that names no code carries none.
 */
function capacityCodes(body: unknown): string[] {
  const record =
    typeof body === "object" && body !== null && !Array.isArray(body)
      ? (body as { codes?: unknown })
      : null;
  if (!Array.isArray(record?.codes)) return [];
  return record.codes.filter((code): code is string => typeof code === "string");
}

/**
 * How the session cookie is attached.
 *
 * Production is one origin, so `same-origin` is enough and a cross-site request
 * does not carry the cookie. Development publishes the API on another port:
 * that is a different origin, and `same-origin` would leave the cookie behind.
 * The decision is the resolved API origin against the page origin. A relative
 * base such as `/api/v1` resolves to the page and stays `same-origin`.
 */
export function credentialMode(pageUrl: string, apiBase: string): RequestCredentials {
  try {
    const page = new URL(pageUrl);
    const api = new URL(apiBase, page);
    if (api.origin !== page.origin) return "include";
  } catch {
    return "same-origin";
  }
  return "same-origin";
}

/**
 * A path the browser may return to after sign-in.
 *
 * Only a same-origin path. A scheme, a protocol-relative URL, a backslash, or
 * a control character is refused, including after percent-decoding. The sign-in
 * page itself is not a destination, or a successful sign-in would loop.
 */
export function safeReturnPath(raw: string | null | undefined): string {
  if (raw == null) return "/";
  const trimmed = raw.trim();
  if (trimmed === "" || !trimmed.startsWith("/") || trimmed.startsWith("//") || trimmed.startsWith("/\\")) {
    return "/";
  }
  if (trimmed.includes("\\") || trimmed.includes("://")) return "/";
  let decoded = trimmed;
  try {
    decoded = decodeURIComponent(trimmed);
  } catch {
    return "/";
  }
  if (
    !decoded.startsWith("/") ||
    decoded.startsWith("//") ||
    decoded.includes("\\") ||
    decoded.includes("://") ||
    /[\u0000-\u001f\u007f]/.test(decoded)
  ) {
    return "/";
  }
  let url: URL;
  try {
    url = new URL(decoded, "http://localhost");
  } catch {
    return "/";
  }
  if (url.origin !== "http://localhost" || url.username || url.password) return "/";
  const path = `${url.pathname}${url.search}`;
  if (path === "/login" || path.startsWith("/login?") || path.startsWith("/login/")) return "/";
  return path;
}

/**
 * Where a 401 sends the browser, or null when the page should keep the error.
 *
 * Sign-in answers 401 for a wrong password, and a password change answers 401
 * for a wrong current password while the session is still valid. Those stay on
 * the page. Every other 401 is a missing session.
 */
export function loginRedirectFor(
  status: number,
  path: string,
  method: string | undefined,
  detail: unknown,
  here: string,
): string | null {
  if (status !== 401) return null;
  const verb = (method ?? "GET").toUpperCase();
  const target = path.split("?")[0];
  if (verb === "POST" && target === "/auth/login") return null;
  if (detail === "current password is wrong") return null;
  if (here === "/login" || here.startsWith("/login?") || here.startsWith("/login/")) return null;
  const next = safeReturnPath(here);
  return `/login?next=${encodeURIComponent(next)}`;
}

/** Where sign-in lands. A password that must change goes to that page first. */
export function destinationAfterLogin(next: string | null | undefined, mustChange: boolean): string {
  const safe = safeReturnPath(next);
  if (!mustChange) return safe;
  if (safe === "/") return "/account/password";
  return `/account/password?next=${encodeURIComponent(safe)}`;
}

/** Where a completed password change lands. The password route is not a home. */
export function destinationAfterPasswordChange(next: string | null | undefined, here: string): string {
  if (next != null && next !== "") return safeReturnPath(next);
  const stay = safeReturnPath(here);
  if (stay === "/account/password" || stay.startsWith("/account/password?")) return "/";
  return stay;
}

/** The browser navigation a missing session uses. Tests replace `assign`. */
export const sessionNavigation = {
  assign(url: string) {
    window.location.assign(url);
  },
};

function requestCredentials(): RequestCredentials {
  if (typeof window === "undefined") return "same-origin";
  return credentialMode(window.location.href, baseUrl());
}

/**
 * One request through the backend. Exported for the saved-dashboards client. A `204` has no body,
 * so it resolves to `undefined` instead of failing in `res.json()`; no existing route answers 204.
 * The session cookie is sent. No token is stored by this client.
 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`, {
    ...init,
    credentials: requestCredentials(),
    // A bodyless read is a simple request. `Content-Type: application/json` is not a CORS-safelisted
    // value, so sending it on a `GET` turns every cross-origin read into a preflighted one; it goes
    // out only when there is a body. A caller's own header still wins.
    headers:
      init?.body === undefined
        ? init?.headers
        : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    let raw: unknown;
    let body: unknown;
    try {
      body = await res.json();
      raw = (body as { detail?: unknown } | null)?.detail;
      if (typeof raw === "string") detail = raw;
    } catch {
      /* non-JSON error body */
    }
    if (typeof window !== "undefined") {
      const target = loginRedirectFor(
        res.status,
        path,
        init?.method,
        raw,
        `${window.location.pathname}${window.location.search}`,
      );
      if (target) {
        try {
          sessionNavigation.assign(target);
        } catch {
          /* jsdom refuses navigation; the caller still receives the error */
        }
      }
    }
    throw new ApiError(res.status, detail, raw, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// --- history backfill (HISTORY-BACKFILL §B.10) ------------------------------
// Mirrors `app/schemas/history_backfill.py`. A measurement that is missing is `null`, never zero.
export type HistoryFamilyKey = "PHASE_A" | "WAF" | "IPS" | "CATCHALL" | "RULE_MATCH";
export type HistoryConsoleMode = "UNDECIDED" | "NO" | "ACTIVE" | "STOPPED";
export type HistoryState =
  | "NOT_REQUESTED"
  | "WAITING"
  | "READING"
  | "COMPLETE"
  | "PARTIAL"
  | "UNAVAILABLE"
  | "NOT_ELIGIBLE";

export interface HistoryDecision {
  answer: "YES" | "NO";
  days: number | null;
  decided_at: string;
  decided_by: string;
  source: string;
  updated_at: string | null;
  updated_by: string | null;
  start_requested_at: string | null;
  start_requested_by: string | null;
  stop_requested_at: string | null;
  stop_requested_by: string | null;
}

export interface HistoryWindow {
  days_requested: number;
  days: number | null;
  available: boolean;
  limited_by: string[];
  reason: string | null;
}

export interface HistoryGap {
  start: string;
  end: string;
  reason: string;
}

/** One log source of a console-wide reading: its key and, when known, its name. */
export interface HistoryScopeSource {
  /** The key the per-source floors are stored under (the QRadar log-source id). */
  key: string;
  /** The log source's name; null when the source is no longer known. */
  name: string | null;
}

export interface HistoryScope {
  scope_key: string;
  /** The log source's name for a WAF or IPS scope row; null for a console-wide row. */
  name?: string | null;
  state: HistoryState | string;
  days_requested: number | null;
  days_target: number | null;
  target_start: string | null;
  floor: string | null;
  /** True when `floor` is the floor QRadar proved; false when it is only where reading started. */
  floor_proved: boolean;
  through: string | null;
  anchor: string | null;
  started_at: string | null;
  finished_at: string | null;
  reason: string | null;
  last_error_class: string | null;
  last_error_at: string | null;
  searches: number | null;
  buckets_read: number | null;
  days_read: number | null;
  gaps: HistoryGap[] | null;
  /**
   * The monitored sources the reading was short of (source volume and catch-all only).
   * Null means "not measured": another family, a scope that has not searched, a stored
   * progress that did not read back. The lists hold at most 25 names; the counts are
   * the whole numbers. The API always sends all four fields.
   */
  sources_cut: HistoryScopeSource[] | null;
  sources_silent: HistoryScopeSource[] | null;
  sources_cut_count: number | null;
  sources_silent_count: number | null;
  progress_unreadable: boolean;
  later_yes: string;
}

export interface HistorySource {
  scope_key: string;
  name: string;
  profile_enabled: boolean;
  later_yes: string;
}

export interface HistoryFamily {
  family: HistoryFamilyKey | string;
  collection_enabled: boolean;
  collection_reason: string;
  window: HistoryWindow;
  scopes: HistoryScope[];
  sources: HistorySource[] | null;
  later_yes: string | null;
}

export interface HistoryConsole {
  instance_id: string;
  name: string;
  decision: HistoryDecision | null;
  mode: HistoryConsoleMode;
  families: HistoryFamily[];
  message: string | null;
}

export interface HistoryFamilyEstimate {
  family: HistoryFamilyKey | string;
  searches: number | null;
  ticks: number | null;
  hours: number | null;
  days_requested: number;
  days_fitted: number | null;
  reduced: boolean;
  refused: boolean;
  reason: string;
  assumptions: string[];
}

export interface HistoryConsoleEstimate {
  instance_id: string;
  name: string;
  kind: "estimate";
  families: HistoryFamilyEstimate[];
}

export type HistoryAnswer = "YES" | "NO";

// --- types mirror the backend Pydantic response schemas -------------------
export interface SocOverview {
  instance_status: string;
  instance_version: string | null;
  instance_last_health_check_at?: string | null;
  generated_at: string;
  log_sources: {
    total_log_sources: number;
    monitored_log_sources: number;
    silent_log_sources: number;
    anomalous_log_sources: number;
    in_maintenance: number;
  };
  offenses: {
    active: number | null;
    critical: number | null;
    critical_unknown?: number | null;
    unassigned: number | null;
    oldest_age_seconds: number | null;
  };
  alerts: { open: number; acknowledged: number; by_severity: Record<string, number> };
}

export interface LogSourceSummary {
  id: string;
  qradar_id: number;
  name: string;
  type_name: string | null;
  criticality: string;
  owner: string | null;
  enabled: boolean;
  monitoring_enabled: boolean;
  maintenance_mode: boolean;
  qradar_status: string | null;
  last_event_time: string | null;
  open_anomaly_count: number;
}

export interface LogSourceDetail {
  id: string;
  // These five are non-nullable in the backend's LogSourceDetail schema and
  // have always been in the response; the type simply did not declare them.
  // Adding them is a frontend correction, not a contract change.
  qradar_id: number;
  enabled: boolean;
  monitoring_enabled: boolean;
  maintenance_mode: boolean;
  timezone_name: string;
  name: string;
  type_name: string | null;
  description: string | null;
  criticality: string;
  owner: string | null;
  owner_email: string | null;
  qradar_status: string | null;
  business_hours_only: boolean;
  expected_interval_seconds: number | null;
  last_event_time: string | null;
  /** Not on the schema today; read when the backend sends it (T-044 report). */
  instance_id?: string | null;
}

// --- Search, log-source, offense and coverage types ------------------------
export interface ScheduledSearch {
  id: string;
  name: string;
  description: string | null;
  owner: string | null;
  category: string | null;
  mitre_techniques: string[];
  aql_query: string;
  query_version: number;
  schedule_cron: string;
  schedule_timezone: string;
  severity: string;
  threshold_value: number | null;
  threshold_operator: string;
  timeout_seconds: number;
  max_time_range_hours: number;
  max_result_rows: number;
  visualization_type: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  consecutive_failures: number;
}

export interface ScheduledSearchCreate {
  name: string;
  description: string | null;
  aql_query: string;
  schedule_cron: string;
  schedule_timezone: string;
  threshold_value: number | null;
  threshold_operator: string;
  severity: string;
  timeout_seconds: number;
  max_time_range_hours: number;
  max_result_rows: number;
  visualization_type: string;
  /** New definitions are always drafts; the editor sends false explicitly. */
  enabled: false;
}

export type ScheduledSearchUpdate = Omit<ScheduledSearchCreate, "name" | "enabled"> & {
  change_note: string | null;
};

export interface SearchExecution {
  id: string;
  search_id: string;
  query_version: number;
  status: string;
  trigger: string;
  triggered_by: string | null;
  ariel_search_id: string | null;
  ariel_status: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  result_count: number | null;
  truncated: boolean;
  error_type: string | null;
  error_message: string | null;
  retry_count: number;
  threshold_breached: boolean;
}

export type SearchFamily =
  | "PHASE_A_VOLUME"
  | "WAF_COLLECTION"
  | "ANOMALY_EXPLANATION"
  | "RULE_MATCH"
  | "IPS_COLLECTION"
  | "CATCHALL_SENDERS"
  | "GEO_COLLECTION"
  | "PROPERTY_WATCH";

// Mirrors CadenceOut: one family's timing, as the Search cadence route computes it. A null bucket
// is a family with no buckets (evidence only), never zero.
export interface Cadence {
  family: SearchFamily;
  cadence_seconds: number;
  bucket_seconds: number | null;
  settle_seconds: number;
}

// Mirrors SearchCadenceLastRunOut. `searches_sent` is null for a family that
// does not count its searches yet: that is "not counted", never zero.
export interface SearchCadenceLastRun {
  at: string;
  outcome: string | null;
  searches_sent: number | null;
  buckets_collected: number | null;
  split_count: number | null;
}

export interface SearchCadenceItem {
  family: SearchFamily;
  cadence_seconds: number;
  allowed_cadences_seconds: number[];
  // Null for a family with no buckets (anomaly explanations).
  bucket_seconds: number | null;
  settle_seconds: number;
  last_slot_start: string | null;
  next_dispatch_at: string;
  updated_at: string;
  updated_by: string | null;
  // Null when the family has not run since the upgrade.
  last_run: SearchCadenceLastRun | null;
}

export interface SearchVersion {
  version: number;
  aql_query: string;
  changed_by: string | null;
  change_note: string | null;
  created_at: string;
}

// --- WAF source administration (docs/FIELD-MAPPING.md §FM.3, §FM.4, §FM.6) ---
// These mirror the field-mapping contract. Every measurement the validation
// report may lack is `| null`: a share that was not measured is not 0 %.

export interface MappingFieldOut {
  key: string;
  label: string;
  description: string;
  required: boolean;
  used_by_collection: boolean;
  kinds: string[];
  normalized_options: string[];
  default: string | null;
}

export interface MappingValueMapOut {
  field: string;
  outcomes: string[];
}

export interface MappingSchemaOut {
  domain: "WAF" | "IPS";
  property_name_pattern: string;
  /** The share of events a collection field must populate (waf_validation_min_ratio). */
  min_coverage_ratio: number;
  fields: MappingFieldOut[];
  value_maps: MappingValueMapOut[];
}

/** Logical field key to the QRadar property it is read from; null = not mapped. */
export type FieldMapping = Record<string, string | null>;

/** Outcome (`BLOCKED`, `ALLOWED`) to the raw action values assigned to it. */
export type ActionValueMap = Record<string, string[]>;

export type WafValidationStatus = "UNVALIDATED" | "VALID" | "INVALID" | "INCONCLUSIVE";

export interface DiscoveredValue {
  value: string;
  events: number;
}

export interface BackendMessageRef {
  code: string;
  params: Record<string, unknown>;
}

export interface FieldValidationOut {
  field: string;
  property_name: string | null;
  /** AVAILABLE, NOT_MAPPED, PROPERTY_REJECTED, UNPOPULATED, INSUFFICIENT_COVERAGE,
   *  IMPLAUSIBLE, NO_EVENTS or QUERY_FAILED. Typed as string so a status this
   *  build does not know is shown as recorded rather than dropped. */
  status: string;
  used_by_collection: boolean;
  populated_events: number | null;
  populated_ratio: number | null;
  distinct_values: number | null;
  plausible_ratio: number | null;
  sampled_events: number | null;
  values_truncated: boolean;
  discovered_values: DiscoveredValue[];
  detail: string | null;
  detail_code?: string | null;
  detail_params?: Record<string, unknown> | null;
}

export interface ValidationReportOut {
  status: WafValidationStatus;
  reasons: string[];
  reason_codes?: (BackendMessageRef | null)[] | null;
  total_events: number | null;
  window_start: string;
  window_end: string;
  profile_version: number;
  fields: FieldValidationOut[];
}

export interface WafProfileIn {
  /** Null only to create; otherwise the version the edit was made against. */
  expected_profile_version: number | null;
  product_label: string | null;
  mapping: FieldMapping;
  action_value_map: ActionValueMap;
}

export interface WafProfileOut {
  profile_id: string;
  instance_id: string;
  log_source_id: string;
  qradar_log_source_id: number;
  log_source_name: string;
  log_source_type_name: string | null;
  product_label: string | null;
  profile_version: number;
  enabled: boolean;
  mapping: FieldMapping;
  action_value_map: ActionValueMap;
  validation_status: WafValidationStatus;
  validated_profile_version: number | null;
  validated_at: string | null;
  validation: ValidationReportOut | null;
  validation_pending: boolean;
  validation_requested_at: string | null;
  validation_requested_by: string | null;
  collection_eligible: boolean;
  ineligible_reason: string | null;
  ineligible_reason_code?: string | null;
  ineligible_reason_params?: Record<string, unknown> | null;
  updated_by: string | null;
  updated_at: string;
}

export interface WafProfileHistoryEntryOut {
  at: string;
  actor: string;
  /** waf.profile.created | mapping_changed | relabelled | validation_requested |
   *  validated | enabled | disabled. Unknown values are shown as recorded. */
  action: string;
  profile_version: number | null;
  mapping_before: FieldMapping | null;
  mapping_after: FieldMapping | null;
  outcome: string | null;
  reasons: string[] | null;
  reason_codes?: (BackendMessageRef | null)[] | null;
}

export interface WafCandidateOut {
  log_source_id: string;
  qradar_id: number;
  name: string;
  type_name: string | null;
  source_class: string | null;
  instance_id: string;
}

export interface CatalogProperty {
  name: string;
  property_type: string | null;
  applies_to_source_type: boolean | null;
  name_allowed: boolean;
}

export interface PropertyCatalogOut {
  status: "AVAILABLE" | "UNAVAILABLE";
  fetched_at: string | null;
  filtered_by_source_type: boolean;
  properties: CatalogProperty[];
  detail: string | null;
}

// Mirrors the fields of WafSourceStatusOut the administration list reads.
export interface WafSourceStatus {
  profile_id: string;
  instance_id: string;
  log_source_id: string;
  qradar_log_source_id: number;
  log_source_name: string;
  product_label: string | null;
  enabled: boolean;
  profile_version: number;
  validation_status: WafValidationStatus;
  validated_profile_version: number | null;
  validated_at: string | null;
  validation_fields: { field: string; property_name: string | null; status: string }[];
  collection_eligible: boolean;
  ineligible_reason: string | null;
  ineligible_reason_code?: string | null;
  ineligible_reason_params?: Record<string, unknown> | null;
  validation_pending: boolean;
  validation_requested_at: string | null;
  validation_requested_by: string | null;
  last_bucket?: WafLastBucket | null;
}

/** The newest stored bucket of a WAF source, as `GET /waf/sources` reports it. */
export interface WafLastBucket {
  bucket_start: string;
  completeness: string;
  origin?: HistoryOrigin;
}

export interface Alert {
  id: string;
  fingerprint: string;
  title: string;
  description: string | null;
  severity: string;
  status: string;
  source_type: string;
  opened_at: string;
  first_seen_at: string;
  last_seen_at: string | null;
  acknowledged_by: string | null;
  resolved_by: string | null;
  resolution_reason: string | null;
  occurrence_count: number;
  evidence_snapshot: Record<string, unknown>;
  source_anomaly_ids: string[];
}

// One stored aggregate joined to the execution that produced it. `query_version`
// is per point so the chart can mark where the AQL changed — results either side
// of a query change are not comparable.
export interface ResultMetricPoint {
  bucket_start: string;
  metric_key: string;
  value: number;
  dimensions: Record<string, unknown>;
  execution_id: string;
  execution_status: string;
  duration_ms: number | null;
  result_count: number | null;
  threshold_breached: boolean;
  query_version: number;
  query_version_id: string | null;
}

export interface SearchResultTrend {
  search_id: string;
  metric_key: string;
  threshold_value: number | null;
  threshold_operator: string;
  count: number;
  points: ResultMetricPoint[];
}

/** One stored metric key of a search (`GET /searches/{id}/metric-keys`). */
export interface MetricKeyStat {
  metric_key: string;
  point_count: number;
  last_bucket_start: string;
}

export interface MetricKeyList {
  search_id: string;
  /** Distinct keys before the limit. */
  total: number;
  /** Keys this response holds. */
  count: number;
  keys: MetricKeyStat[];
}

/** One grouped aggregate of the latest execution that stored a row. */
export interface LatestGroupRow {
  metric_key: string;
  dimensions: Record<string, unknown>;
  value: number;
}

export interface LatestGroups {
  search_id: string;
  execution_id: string | null;
  started_at: string | null;
  status: string | null;
  truncated: boolean;
  /** True when each value is a floor: the UI shows "≥ n" and no verdict. */
  values_are_lower_bounds: boolean;
  /** Why `rows` is empty. An unknown search is 404, not one of these. */
  empty: "no_execution" | "no_rows" | null;
  /** Rows on that execution before the limit. */
  total: number;
  rows: LatestGroupRow[];
}

export interface Notification {
  id: string;
  alert_id: string;
  channel: string;
  target: string;
  transition: string;
  status: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  sent_at: string | null;
  error_message: string | null;
}

// --- Phase 3 types ---------------------------------------------------------
// Every list endpoint returns this envelope; the pages page through it rather
// than fetching an unbounded list.
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface OffenseSummary {
  instance_id: string;
  qradar_offense_id: number;
  captured_at: string;
  description: string | null;
  status: string;
  magnitude: number | null;
  severity: number | null;
  credibility: number | null;
  relevance: number | null;
  assigned_to: string | null;
  is_assigned: boolean;
  offense_type_name: string | null;
  offense_source: string | null;
  source_network: string | null;
  event_count: number | null;
  flow_count: number | null;
  source_count: number | null;
  destination_count: number | null;
  start_time: string | null;
  last_updated_time: string | null;
  close_time: string | null;
  age_seconds: number | null;
  categories: string[];
}

export interface OffenseDetail extends OffenseSummary {
  usernames: string[];
  source_addresses: string[];
  local_destination_addresses: string[];
  rule_ids: number[];
  log_source_ids: number[];
  destination_networks: string[];
}

export interface OffenseHistoryPoint {
  captured_at: string;
  status: string;
  magnitude: number | null;
  severity: number | null;
  event_count: number | null;
  assigned_to: string | null;
}

export interface OffenseAggregates {
  active: number | null;
  critical: number | null;
  unassigned: number | null;
  exceeding_sla: number | null;
  oldest_age_seconds: number | null;
  average_magnitude: number | null;
  /** Open offenses with no magnitude (in neither `critical` nor the rest); null: no snapshot. */
  critical_unknown?: number | null;
  /** Open offenses with no age (not in `exceeding_sla`); null: no snapshot. */
  age_unknown?: number | null;
  sla_hours: number;
  critical_magnitude: number;
}

export interface CountEntry {
  key: string;
  count: number;
}

export interface AgingBucket {
  bucket: string;
  count: number | null;
}

export interface OffenseAnalytics {
  generated_at: string;
  aggregates: OffenseAggregates;
  aging: AgingBucket[];
  status_distribution: CountEntry[];
  magnitude_distribution: CountEntry[];
  category_distribution: CountEntry[];
  assignment_distribution: CountEntry[];
  top_rules: CountEntry[];
  top_usernames: CountEntry[];
  top_source_ips: CountEntry[];
  top_destination_ips: CountEntry[];
}

export interface RuleSummary {
  id: string;
  qradar_id: number;
  name: string;
  enabled: boolean;
  is_building_block: boolean;
  rule_type: string | null;
  origin: string | null;
  owner: string | null;
  health_status: string | null;
  health_evaluated_at: string | null;
  // The reason stored with the verdict above; null when that evaluation recorded none.
  health_reason: string | null;
  // The code and raw values `health_reason` was rendered from; null on a snapshot written
  // before they were stored.
  health_reason_code?: string | null;
  health_reason_params?: Record<string, unknown> | null;
  last_fired_at: string | null;
  offense_contribution_count: number;
  event_contribution_count: number;
  mitre_techniques: string[];
}

export interface RuleDependency {
  kind: string;
  target_ref: string;
  target_name: string | null;
  source: string;
  confidence: number;
  provenance: string | null;
}

export interface RuleDetail extends RuleSummary {
  description: string | null;
  categories: string[];
  average_capacity: number | null;
  generates_offense: boolean | null;
  response_actions: string[];
  qradar_created_at: string | null;
  qradar_modified_at: string | null;
  first_seen_at: string | null;
  soc_notes: string | null;
  expected_daily_firings: number | null;
  health_monitoring_enabled: boolean;
  dependencies: RuleDependency[];
}

export interface RuleHealthSnapshot {
  evaluated_at: string;
  status: string;
  logic_version: number;
  confidence: number;
  reason: string | null;
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  window_start: string | null;
  window_end: string | null;
  last_triggered_at: string | null;
  trigger_count: number;
  offense_contribution_count: number;
  enabled: boolean;
  building_blocks_healthy: boolean | null;
  required_log_sources_healthy: boolean | null;
  missing_dependencies: string[];
  evidence: Record<string, unknown>;
}

export interface RuleHealthCount {
  status: string;
  count: number;
}

export interface CoverageSummary {
  generated_at: string;
  total_techniques: number;
  by_status: Record<string, number>;
  covered_ratio: number;
  average_coverage_score: number;
  mapping_provenance: { explicit: number; inferred: number };
}

export interface TechniqueCoverage {
  technique_id: string;
  technique_name: string | null;
  tactic: string | null;
  status: string;
  coverage_score: number | null;
  confidence: number | null;
  reason: string | null;
  // The code and raw values `reason` was built from; absent or null on a row stored before them.
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  mapped_rule_count: number;
  enabled_rule_count: number;
  firing_rule_count: number;
  inferred_rule_count: number;
  degraded_rule_count: number;
  last_evaluated_at: string | null;
}

export interface RuleCoverageView {
  rule_id: string;
  qradar_id: number;
  name: string;
  enabled: boolean;
  health_status: string;
  techniques: Array<{
    technique_id: string;
    technique_name: string | null;
    tactic: string | null;
    source: string;
    confidence: number;
    provenance: string | null;
  }>;
}

export interface DataSourceCoverageView {
  kind: string;
  target_ref: string;
  target_name: string | null;
  rules: Array<{
    rule_id: string;
    qradar_id: number;
    name: string;
    enabled: boolean;
    health_status: string;
    dependency_source: string;
    dependency_confidence: number;
  }>;
  techniques: string[];
}

// --- Phase A types: behavioral analytics ----------------------------------
// These mirror app/schemas/anomaly.py exactly, including its nullability. The
// backend's rule is that a value which was not measured is `null`, never 0 —
// so every optional number here is `number | null` rather than `number`, and no
// field is widened to `unknown` or `any`. Narrowing one of these to a plain
// `number` would let a missing measurement render as a real zero.

export type AnomalyState =
  | "INSUFFICIENT_DATA"
  | "NORMAL"
  | "CANDIDATE"
  | "OPEN"
  | "RECOVERING"
  | "RESOLVED"
  | "SUPPRESSED";

/**
 * Where a stored bucket came from: the cadence collection, or QRadar's past (learning only).
 * Every `origin` below is optional only because a response from before HB-14 carries none; the
 * charts then say nothing about origin. `null` is unknown and is never rendered as live.
 */
export type HistoryOrigin = "LIVE" | "HISTORY";

export type BucketCompleteness = "COMPLETE" | "PARTIAL" | "MISSING";

export type EvidenceStatus =
  | "NOT_REQUESTED"
  | "PENDING"
  | "COMPLETE"
  | "PARTIAL"
  | "UNAVAILABLE"
  | "FAILED";

export type DimensionAvailability =
  | "AVAILABLE"
  | "TRUNCATED"
  | "UNAVAILABLE"
  | "FAILED";

export type RobustScoreStatus = "OK" | "DEGENERATE" | "UNAVAILABLE";

export interface MetricBucket {
  bucket_start: string;
  bucket_seconds: number;
  // Where the bucket was read from. HISTORY: read from QRadar's past, for learning only.
  origin?: HistoryOrigin;
  event_count: number;
  average_eps: number;
  // Null when the bucket's collection path does not measure a peak. The live
  // Ariel aggregate never does, so this is null for every live bucket.
  peak_eps: number | null;
  completeness: BucketCompleteness;
  last_event_at: string | null;
}

export interface BaselineCell {
  metric_name: string;
  weekday: number;
  hour: number;
  median: number;
  mad: number;
  p05: number | null;
  p95: number | null;
  sample_count: number;
  // False means the cell exists but is still learning. Never render as healthy.
  is_reliable: boolean;
  completeness: number;
  baseline_version: number;
  computed_at: string | null;
}

export interface SourceBehavior {
  log_source_id: string;
  name: string;
  criticality: string;
  observed_eps: number | null;
  expected_eps: number | null;
  expected_low: number | null;
  expected_high: number | null;
  // null when expected is zero: a ratio against nothing does not exist.
  deviation_ratio: number | null;
  state: AnomalyState;
  baseline_sample_count: number;
  baseline_completeness: number;
  last_bucket_at: string | null;
  // The completeness of the bucket `observed_eps` came from: COMPLETE, PARTIAL
  // or MISSING; null when no bucket exists. A client must not rank or present
  // a non-COMPLETE value as a measurement.
  last_bucket_completeness?: "COMPLETE" | "PARTIAL" | "MISSING" | null;
  last_event_at: string | null;
  open_anomaly_count: number;
  // None when this source has never been evaluated and has no fault row.
  evaluation?: SourceEvaluation | null;
  // Given-up buckets in the selected window. A measured zero is 0.
  skipped_buckets_in_window?: number | null;
  skipped_buckets?: SkippedBucket[];
}

export interface SourceEvaluation {
  judged_through: string | null;
  failed_bucket_start: string | null;
  attempts: number | null;
  max_attempts: number | null;
  error_class: string | null;
  last_failure_at: string | null;
  given_up_at: string | null;
  resolved_at: string | null;
}

export interface SkippedBucket {
  bucket_start: string;
  error_class: string;
  attempts: number;
  given_up_at: string | null;
  last_failure_at: string;
}

export interface AnomalyTransition {
  from_state: AnomalyState | null;
  to_state: AnomalyState;
  occurred_at: string;
  bucket_start: string | null;
  reason: string | null;
  // The registry code of `reason` and its raw values (absent or null: no code, show `reason`).
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  actor: string;
  observed_value: number | null;
  expected_value: number | null;
}

export interface AnomalySummary {
  id: string;
  // A PW_VALUE row whose watch is scoped to a type or group has no single source.
  log_source_id: string | null;
  log_source_name: string | null;
  anomaly_type: string;
  state: AnomalyState;
  severity: string;
  observed_value: number | null;
  expected_value: number | null;
  deviation_ratio: number | null;
  robust_z: number | null;
  absolute_delta: number | null;
  consecutive_buckets: number;
  confidence: number | null;
  detected_at: string;
  opened_at: string | null;
  anomaly_start: string | null;
  anomaly_end: string | null;
  resolved_at: string | null;
  // null while the anomaly is still running — not 0, and not "now minus start".
  duration_seconds: number | null;
  evidence_status: EvidenceStatus;
  suppressed: boolean;
  explanation: string | null;
  // The registry code of `explanation` and its raw values (property-watch rows today).
  explanation_code?: string | null;
  explanation_params?: Record<string, unknown> | null;
  // The unified list (T-057). Absent on a Phase A row from an older response, so
  // a missing `subject_kind` is read as LOG_SOURCE.
  subject_kind?: SubjectKind;
  waf_endpoint?: WafEndpointRef | null;
  // IPS_ENTITY only (T-085): the admitted entity. An IPS_SOURCE row has none.
  ips_entity?: IpsEntityRef | null;
  // PW_VALUE only (Phase G): the watch and the tracked value. `log_source_id` is null for a
  // type- or group-scoped watch.
  pw_value?: PwValueRef | null;
  // WAF and IPS: EXACT, or LOWER_BOUND when observed_value is "at least n".
  observed_measurement?: MeasurementKind | null;
}

export interface Contributor {
  dimension: string;
  value: string;
  label: string | null;
  baseline_count: number;
  anomaly_count: number;
  absolute_delta: number;
  // null for a newly observed value: it has no baseline to be a percent of.
  percent_delta: number | null;
  anomaly_share: number | null;
  baseline_share: number | null;
  contribution_share: number | null;
  baseline_rank: number | null;
  anomaly_rank: number | null;
  rank: number;
  is_new: boolean;
  is_disappeared: boolean;
}

export interface ExplanationDimension {
  dimension: string;
  availability: DimensionAvailability;
  detail: string | null;
  detail_code?: string | null;
  detail_params?: Record<string, unknown> | null;
  baseline_distinct_count: number | null;
  anomaly_distinct_count: number | null;
  cardinality_ratio: number | null;
  new_value_count: number;
  disappeared_value_count: number;
  baseline_top_share: number | null;
  anomaly_top_share: number | null;
  truncated: boolean;
  contributors: Contributor[];
}

// Sanitized query provenance: AQL text, row counts and window bounds. Non-secret
// by construction — the backend never puts a token, header or response body
// here. Typed loosely because it is a bounded audit document rather than a
// contract, and it is rendered as text, never interpreted.
export interface QueryProvenanceEntry {
  dimension?: string;
  window?: string;
  aql?: string;
  rows?: number;
  truncated?: boolean;
  error?: string | null;
}

export interface QueryProvenance {
  comparison_strategy?: string;
  anomaly_window?: { start?: string; end?: string; seconds?: number };
  baseline_window?: { start?: string; end?: string; seconds?: number };
  queries?: QueryProvenanceEntry[];
}

export interface ExplanationPackage {
  status: EvidenceStatus;
  error: string | null;
  anomaly_window_start: string;
  anomaly_window_end: string;
  baseline_window_start: string;
  baseline_window_end: string;
  comparison_strategy: string;
  anomaly_total_events: number;
  baseline_total_events: number;
  requested_at: string | null;
  completed_at: string | null;
  collection_duration_ms: number | null;
  query_provenance: QueryProvenance;
  schema_version: number;
  dimensions: ExplanationDimension[];
}

// The detector's own working. Every field is optional: an older anomaly, or one
// from a detector that does not compute a given quantity, has no value for it,
// which must render as "not recorded" rather than as 0.
export interface DetectionDetail {
  reason: string | null;
  expected_low: number | null;
  expected_high: number | null;
  threshold: number | null;
  baseline_sample_count: number | null;
  baseline_completeness: number | null;
  baseline_version: number | null;
  observed_eps: number | null;
  expected_eps: number | null;
  observed_events: number | null;
  expected_events: number | null;
  absolute_delta_events: number | null;
  bucket_seconds: number | null;
  ratio: number | null;
  ratio_basis: string | null;
  robust_score_status: RobustScoreStatus | null;
  robust_z: number | null;
  fallback_bound: number | null;
}

export interface AnomalyDetail extends AnomalySummary {
  baseline_version: number | null;
  policy_version: number;
  transitions: AnomalyTransition[];
  // null when evidence was never requested or has not been collected yet.
  explanation_package: ExplanationPackage | null;
  detection: DetectionDetail | null;
}

export interface BehaviorSummary {
  open_anomalies: number;
  spikes: number;
  drops: number;
  silent_sources: number;
  candidates: number;
  recovering: number;
  // Counted apart from healthy sources: an unbaselined source is an
  // observability gap, not a clean bill of health.
  insufficient_data_sources: number;
  monitored_sources: number;
  evidence_pending: number;
  evidence_failed: number;
  // Sources with an open evaluation fault or a given-up bucket in 24 h.
  sources_with_evaluation_faults: number;
  // Given-up buckets in the last 24 h. These buckets have no verdict.
  buckets_not_judged: number;
  // Anomalies with a recorded resolution timestamp in the last 24 h.
  resolved_last_24h: number;
  recently_resolved: AnomalySummary[];
  highest_deviation: AnomalySummary[];
}

export interface AnomalyListParams {
  limit?: number;
  offset?: number;
  log_source_id?: string;
  instance_id?: string;
  anomaly_type?: string;
  state?: string;
  severity?: string;
  evidence_status?: string;
  active_only?: boolean;
  subject_kind?: SubjectKind | SubjectKind[];
  waf_endpoint_id?: string;
  ips_entity_id?: string;
  // A property watch's incidents (Phase G).
  property_watch_id?: string;
  since?: string;
  until?: string;
}

// --- Phase B types: WAF endpoint behavior ----------------------------------
// These mirror app/schemas/anomaly.py (T-057). A missing measurement is `null`,
// never 0: narrowing one of these to `number` would let it render as a zero.

export type SubjectKind =
  | "LOG_SOURCE"
  | "WAF_ENDPOINT"
  | "IPS_ENTITY"
  | "IPS_SOURCE"
  | "PW_VALUE";

/** EXACT, or LOWER_BOUND when the count is "at least n". */
export type MeasurementKind = "EXACT" | "LOWER_BOUND";

export interface WafEndpointRef {
  id: string;
  normalized_endpoint: string;
  http_method: string;
  // null when the source exposes no virtual host. Never an empty string.
  virtual_host: string | null;
  // false: the endpoint may combine traffic of applications sharing a path.
  virtual_host_available: boolean;
  normalizer_version: number;
}

export interface WafDetectionDetail {
  reason: string | null;
  // The registry code of `reason` and its raw values; absent on a row stored before codes.
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  expected_low: number | null;
  expected_high: number | null;
  threshold: number | null;
  baseline_sample_count: number | null;
  baseline_completeness: number | null;
  baseline_version: number | null;
  normalizer_version: number | null;
  observed_requests: number | null;
  expected_requests: number | null;
  absolute_delta_requests: number | null;
  endpoint_coverage_ratio: number | null;
  request_count_measurement: MeasurementKind | null;
  observed_proportion: number | null;
  expected_proportion: number | null;
  observed_numerator: number | null;
  observed_denominator: number | null;
  percentage_point_change: number | null;
  caveat?: string | null;
  caveat_code?: string | null;
  caveat_params?: Record<string, unknown> | null;
  ratio: number | null;
  ratio_basis: string | null;
  robust_score_status: RobustScoreStatus | null;
  robust_z: number | null;
  fallback_bound: number | null;
}

/** A WAF subject's detail. Evidence contributors are not served for WAF
 *  (`explanation_package` is null), and `detection` is the WAF block. */
export interface WafAnomalyDetail
  extends Omit<AnomalyDetail, "detection" | "subject_kind" | "waf_endpoint"> {
  subject_kind: "WAF_ENDPOINT";
  waf_endpoint: WafEndpointRef;
  detection: WafDetectionDetail | null;
}

export function isWafDetail(
  a: AnomalyDetail | WafAnomalyDetail | IpsAnomalyDetail | PwAnomalyDetail,
): a is WafAnomalyDetail {
  return a.subject_kind === "WAF_ENDPOINT" && a.waf_endpoint != null;
}

export interface WafMetricBucket {
  bucket_start: string;
  bucket_seconds: number;
  // null: the source's bucket record is missing, so the origin is unknown (never LIVE).
  origin?: HistoryOrigin | null;
  request_count: number | null;
  request_count_measurement: MeasurementKind | null;
  completeness: BucketCompleteness;
  partial: boolean;
  endpoint_coverage_ratio: number | null;
  in_maintenance: boolean | null;
  normalizer_version: number;
  distinct_client_count: number | null;
  // Each breakdown's own status: EXACT, LOWER_BOUND, NOT_COLLECTED or FAILED. Its
  // counts below are null unless it was measured. Absent on an older response.
  action_measurement?: string | null;
  response_code_measurement?: string | null;
  client_measurement?: string | null;
  blocked_count?: number | null;
  action_events?: number | null;
  response_code_events?: number | null;
  client_error_count?: number | null;
  server_error_count?: number | null;
  client_events?: number | null;
  top1_client_requests?: number | null;
}

export interface PagedWafMetrics {
  items: WafMetricBucket[];
  total: number;
  limit: number;
  offset: number;
  range_start: string;
  range_end: string;
}

/** One seasonal cell of an endpoint's baseline (`WafBaselineCellOut`). Weekday is
 *  ISO (1 = Monday) and the hour is UTC, as in Phase A. */
export interface WafBaselineCell {
  metric_name: string;
  weekday: number;
  hour: number;
  median: number;
  mad: number;
  p05: number | null;
  p95: number | null;
  sample_count: number;
  is_reliable: boolean;
  completeness: number;
  baseline_version: number;
  normalizer_version: number;
  excluded_sample_count: number;
  metric_semantics: string | null;
}

export interface PagedWafBaselines {
  items: WafBaselineCell[];
  total: number;
  limit: number;
  offset: number;
}

export interface WafLatestBucket {
  bucket_start: string;
  bucket_seconds: number;
  origin?: HistoryOrigin;
  completeness: string;
  endpoint_coverage_ratio: number;
  // LOWER_BOUND when grouping was truncated: the ratio is then itself a floor.
  coverage_measurement: MeasurementKind;
  request_count_measurement: MeasurementKind;
  total_events: number;
  in_maintenance: boolean;
  groups_truncated: boolean;
  admission_truncated: boolean;
}

export interface WafAdmissionOverflow {
  buckets_in_range: number;
  // null when no bucket was collected in range: "nothing refused" is a claim
  // only a collected bucket can make.
  buckets_admission_truncated: number | null;
  rejected_over_quota: number | null;
  below_admission_threshold: number | null;
  untracked_requests: number | null;
}

export interface WafSourceBehavior {
  log_source_id: string;
  log_source_name: string;
  product_label: string | null;
  enabled: boolean;
  profile_version: number;
  validation_status: string;
  validated_profile_version: number | null;
  validated_at: string | null;
  validation_current: boolean;
  virtual_host_available: boolean;
  latest_bucket: WafLatestBucket | null;
  active_endpoints: number;
  total_endpoints: number;
  active_endpoint_ceiling: number;
  admission: WafAdmissionOverflow;
}

export interface WafAnalystValidationField {
  field: string;
  property_name: string | null;
  status: string;
  used_by_collection: boolean;
  populated_events: number | null;
  populated_ratio: number | null;
  distinct_values: number | null;
  plausible_ratio: number | null;
  sampled_events: number | null;
  values_truncated: boolean | null;
  discovered_value_count: number | null;
  detail: string | null;
  detail_code?: string | null;
  detail_params?: Record<string, unknown> | null;
}

export interface WafAnalystValidationReport {
  status: string | null;
  reasons: string[];
  reason_codes?: (BackendMessageRef | null)[] | null;
  log_source_found: boolean | null;
  total_events: number | null;
  window_start: string | null;
  window_end: string | null;
  profile_version: number | null;
  validated_at: string | null;
  fields: WafAnalystValidationField[];
}

export interface WafSourceDetail extends WafSourceBehavior {
  validation_report?: WafAnalystValidationReport | null;
}

export interface PagedWafSources {
  items: WafSourceBehavior[];
  total: number;
  limit: number;
  offset: number;
  range_start: string;
  range_end: string;
}

export interface WafEndpointInventory {
  id: string;
  normalized_endpoint: string;
  http_method: string;
  virtual_host: string | null;
  normalizer_version: number;
  first_seen_at: string;
  last_seen_at: string;
  is_active: boolean;
  retired_at: string | null;
  tracked_since: string;
  baseline: { cells_total: number; reliable_cells: number };
  baseline_rebuilt_at: string | null;
  // Worst detector state. null = no detector has evaluated it; not NORMAL.
  state: AnomalyState | null;
  active_anomaly_count: number;
}

export interface PagedWafEndpoints {
  items: WafEndpointInventory[];
  total: number;
  limit: number;
  offset: number;
}

// --- Phase D types: IPS behavior ---------------------------------------------
// These mirror app/schemas/anomaly.py and app/schemas/ips_behavior.py (T-085).
// A missing measurement is `null`, never 0. The API serves no contributor list
// and no source IP for IPS, by design (docs/PHASE-D-IPS-BEHAVIOR.md §D.4).

export type IpsDimensionName =
  | "TOTALS"
  | "DESTINATION_PORT"
  | "DESTINATION_IP"
  | "SIGNATURE"
  | "SOURCE_IP"
  | "PORT_FANOUT"
  | "ACTION";

export interface IpsEntityRef {
  id: string;
  dimension: IpsDimensionName;
  // `443/tcp`, a destination address, or `qid:5000123`. Never a source IP.
  value_key: string;
  // The signature's name when the dimension is SIGNATURE; null when none was named.
  display_name: string | null;
}

export interface IpsDetectionDetail {
  reason: string | null;
  expected_low: number | null;
  expected_high: number | null;
  threshold: number | null;
  baseline_sample_count: number | null;
  baseline_completeness: number | null;
  baseline_version: number | null;
  // Entity-rate detectors. The new-port detector expects none: expected_events is null.
  observed_events: number | null;
  expected_events: number | null;
  // Port fan-out: the busiest source IP's distinct destination ports.
  observed_distinct_ports: number | null;
  expected_distinct_ports: number | null;
  // Concentration and block ratio, with the counts the proportion came from.
  observed_proportion: number | null;
  expected_proportion: number | null;
  observed_numerator: number | null;
  observed_denominator: number | null;
  percentage_point_change: number | null;
  observed_measurement: MeasurementKind | null;
  absolute_delta: number | null;
  caveat: string | null;
  ratio: number | null;
  ratio_basis: string | null;
  robust_score_status: RobustScoreStatus | null;
  robust_z: number | null;
  fallback_bound: number | null;
}

/** An IPS subject's detail. There is no evidence package (`explanation_package`
 *  is null) and `detection` is the IPS block. An IPS_SOURCE incident has no entity. */
export interface IpsAnomalyDetail
  extends Omit<AnomalyDetail, "detection" | "subject_kind" | "ips_entity"> {
  subject_kind: "IPS_ENTITY" | "IPS_SOURCE";
  ips_entity: IpsEntityRef | null;
  detection: IpsDetectionDetail | null;
}

/** Decided by the subject kind alone: an IPS row whose entity reference is missing
 *  must still reach the IPS view, never fall through to the EPS page. */
export function isIpsDetail(
  a: AnomalyDetail | WafAnomalyDetail | IpsAnomalyDetail | PwAnomalyDetail,
): a is IpsAnomalyDetail {
  return a.subject_kind === "IPS_ENTITY" || a.subject_kind === "IPS_SOURCE";
}

// --- Phase G: property watches ----------------------------------------------
// These mirror app/schemas/property_watch.py and the PW_VALUE additions in
// app/schemas/anomaly.py. A count that was not measured is `null`, never 0; a reading carries
// its own label so a client never infers "zero" from an absent number. Value text is shown to
// every caller who can read the route (design §G.7.2, D-22) and is attacker-controlled text:
// render it as text only.

export type PwScopeKind = "LOG_SOURCE" | "LOG_SOURCE_TYPE" | "LOG_SOURCE_GROUP";

export type PwWatchStatus =
  | "NOT_ENABLED"
  | "DISABLED"
  | "SCOPE_EMPTY"
  | "SCOPE_STALE"
  | "SCOPE_TOO_LARGE"
  | "NOT_APPLICABLE"
  | "STALE"
  | "READING_HISTORY"
  | "OPEN"
  | "RECOVERING"
  | "OK";

export type PwAnomalyTypeName =
  | "PW_VALUE_SPIKE"
  | "PW_VALUE_DROP"
  | "PW_VALUE_DISAPPEARED"
  | "PW_NEW_VALUE"
  | "PW_NEW_COMBINATION";

/** `COUNT`: a number of events; `MEASURED_ZERO`: zero, measured; `UNKNOWN`: lower bound 0, not a
 *  zero; `NOT_AN_OBSERVATION`: the value was not tracked then; `NO_BUCKET`: nothing was collected. */
export type PwReadingLabel =
  | "COUNT"
  | "MEASURED_ZERO"
  | "UNKNOWN"
  | "NOT_AN_OBSERVATION"
  | "NO_BUCKET";

export type PwBaselineState = "RELIABLE" | "LEARNING" | "NONE";

export interface PwPropertyRef {
  kind: "NORMALIZED" | "CUSTOM_PROPERTY";
  name: string;
}

export interface PwScopeSummary {
  kind: PwScopeKind;
  // null when the inventory no longer lists it.
  name: string | null;
  // A source's type name, a group's path from the root.
  detail: string | null;
  qradar_id: number | null;
  member_count: number | null;
}

export interface PwScopeOption {
  kind: PwScopeKind;
  id: string;
  qradar_id: number;
  name: string;
  detail: string | null;
  synced_source_count: number | null;
  scope_state: PwWatchStatus | null;
  scope_reason: string | null;
  scope_reason_code?: string | null;
  scope_reason_params?: Record<string, unknown> | null;
}

export interface PwPropertyOption {
  name: string;
  property_type: string | null;
  name_allowed: boolean;
  defined_on_type_ids: number[];
  covers_scope: boolean;
}

export interface PwPropertyOptions {
  status: "AVAILABLE" | "UNAVAILABLE";
  fetched_at: string | null;
  scope_type_ids: number[];
  properties: PwPropertyOption[];
  normalized_fields: string[];
  detail: string | null;
}

export interface PwScopeGroup {
  qradar_id: number;
  name: string;
}

export interface PwScopeChange {
  bucket_start: string;
  member_count_before: number | null;
  member_count_after: number | null;
  in_grace_window: boolean;
}

/** `GET /property-watches/{id}/scope` (PW-5b). A scope that did not resolve still returns what the
 *  inventory holds, marked; `member_count` is `null` when the resolver would not state one. */
export interface WatchScope {
  watch_id: string;
  watch_version: number;
  kind: PwScopeKind;
  resolved: boolean;
  state: PwWatchStatus | null;
  reason: string | null;
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  member_count: number | null;
  members: string[];
  members_total: number | null;
  groups_walked: PwScopeGroup[];
  inventory_synced_at: string | null;
  inventory_age_seconds: number | null;
  changes: PwScopeChange[];
}

export interface PwCoverage {
  bucket_start: string;
  total_events: number | null;
  parsed_events: number | null;
  // null when unknown, never 0.
  ratio: number | null;
  measurement: string;
}

export type PwHistoryState =
  | "NOT_REQUESTED"
  | "READING"
  | "COMPLETE"
  | "PARTIAL"
  | "UNAVAILABLE";

export interface PwHistoryGap {
  start: string;
  end: string;
  reason: string;
  // A gap stored before codes carries none.
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
}

export interface PwHistoryEstimate {
  searches_min: number;
  searches_max: number;
  ticks_min: number;
  ticks_max: number;
  cadence_seconds: number;
  assumptions: string;
}

export interface PwWatchHistory {
  state: PwHistoryState;
  days_requested: number | null;
  target_start: string | null;
  floor: string | null;
  through: string | null;
  end: string | null;
  days_read: number | null;
  searches: number | null;
  buckets_read: number | null;
  gaps: PwHistoryGap[] | null;
  reason: string | null;
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  progress_unreadable: boolean;
  started_at: string | null;
  finished_at: string | null;
  estimate: PwHistoryEstimate | null;
}

export interface PropertyWatch {
  id: string;
  instance_id: string;
  name: string;
  description: string | null;
  scope_kind: PwScopeKind;
  log_source_id: string | null;
  scope_type_id: number | null;
  scope_group_id: number | null;
  scope: PwScopeSummary;
  properties: PwPropertyRef[];
  pinned_values: string[][];
  detectors: PwAnomalyTypeName[];
  severity: string;
  watch_version: number;
  enabled: boolean;
  // null: the watch has never run, which is not OK.
  status: PwWatchStatus | null;
  status_reason: string | null;
  status_reason_code?: string | null;
  status_reason_params?: Record<string, unknown> | null;
  collected_through: string | null;
  evaluated_through: string | null;
  last_run_at: string | null;
  coverage: PwCoverage | null;
  open_incidents: number;
  // Only on the single-watch read.
  detector_states: Array<{ detector_type: PwAnomalyTypeName; active_incidents: number }> | null;
  // Only on the single-watch read: the custom rules that pin a value of this watch.
  threshold_rules: PwPinRule[] | null;
  alerting_mode: "shadow" | "alerting";
  // The PROPERTY_WATCH search-family cadence in seconds; the widget is stale after twice this.
  cadence_seconds: number;
  history: PwWatchHistory;
  // The same family's bucket and settle time, for the latency sentence.
  cadence?: Cadence;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

/** `PinRuleOut`: a custom rule that pins one value of the watch. */
export interface PwPinRule {
  id: string;
  name: string;
  pw_value_id: string;
  value_key: string;
  components: string[];
  enabled: boolean;
}

/** `WatchAqlQueryOut`: one generated search, read-only text. Nothing here is ever sent back. */
export interface PwAqlQuery {
  search: "GROUPED" | "TOTALS" | "PINNED";
  text: string;
}

/** `WatchAqlOut` (`GET /property-watches/{id}/aql`). */
export interface WatchAql {
  watch_id: string;
  watch_version: number;
  window_start: string | null;
  window_end: string | null;
  bucket_seconds: number | null;
  current: PwAqlQuery[];
  current_unavailable: string | null;
  current_unavailable_code?: string | null;
  current_unavailable_params?: Record<string, unknown> | null;
  last_bucket: {
    bucket_start: string;
    collected_at: string;
    watch_version: number;
    queries: PwAqlQuery[];
  } | null;
}

export interface PropertyWatchCatalog {
  collection_enabled: boolean;
  alerting_mode: "shadow" | "alerting";
  max_enabled: number;
  enabled_count: number;
  search_budget: number;
  budget_used: number;
  normalized_fields: string[];
  reference_kinds: string[];
  detectors: string[];
  max_properties: number;
  max_values_per_bucket: number;
  max_new_values_per_bucket: number;
  max_active_values_per_watch: number;
  max_pinned_values: number;
  value_max_length: number;
  max_sources_per_search: number;
  max_chunks_per_watch: number;
  // The coverage floor, a fraction in [0, 1].
  min_coverage: number;
  hours_min: number;
  hours_max: number;
  hours_default: number;
  preview_searches: number;
  preview_rate_limit: string;
  history: {
    enabled: boolean;
    days_default: number;
    days_min: number;
    days_max: number;
    searches_per_tick: number;
    seconds_per_tick: number;
    reading_count: number;
    searches_free: number;
  };
  cadence?: Cadence;
}

export interface PwBucket {
  bucket_start: string;
  origin: "LIVE" | "HISTORY";
  bucket_seconds: number;
  watch_version: number;
  total_events: number | null;
  parsed_events: number | null;
  unparsed_events: number | null;
  untracked_events: number | null;
  totals_complete: boolean;
  values_measurement: string;
  pinned_measurement: string;
  observed_values: number;
  tracked_values_observed: number;
  admitted_values: number;
  below_admission_threshold: number;
  rejected_over_quota: number;
  admission_truncated: boolean;
  scope_member_count: number | null;
  scope_changed: boolean;
  in_maintenance: boolean;
  collected_at: string;
}

export interface PwValue {
  id: string;
  watch_version: number;
  value_key: string;
  components: string[];
  truncated: boolean;
  pinned: boolean;
  active: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
  tracked_since: string | null;
  tracked_until: string | null;
  latest_bucket_start: string | null;
  reading: PwReadingLabel;
  events: number | null;
  measurement: MeasurementKind | null;
  baseline_state: PwBaselineState;
  expected_low: number | null;
  expected_median: number | null;
  expected_high: number | null;
  // Reliable hour-of-week cells out of `cells_total`.
  reliable_cells: number;
  cells_total: number;
}

export interface PagedPwValues {
  items: PwValue[];
  total: number;
  limit: number;
  offset: number;
}

export interface PwSeriesPoint {
  bucket_start: string;
  origin: "LIVE" | "HISTORY";
  reading: PwReadingLabel;
  events: number | null;
  measurement: MeasurementKind | null;
  baseline_state: PwBaselineState;
  expected_low: number | null;
  expected_median: number | null;
  expected_high: number | null;
}

export interface PwValueSeries {
  value_id: string;
  value_key: string;
  pinned: boolean;
  // Only collected buckets have a point: a gap is an absent point, never a zero.
  points: PwSeriesPoint[];
}

export interface PwSeries {
  hours: number;
  series: PwValueSeries[];
}

export interface PropertyWatchCreate {
  name: string;
  description: string | null;
  scope_kind: PwScopeKind;
  log_source_id: string | null;
  scope_type_id: number | null;
  scope_group_id: number | null;
  properties: PwPropertyRef[];
  pinned_values: string[][];
  detectors: PwAnomalyTypeName[];
  severity: string;
  history_days?: number;
}

export type PropertyWatchUpdate = Partial<PropertyWatchCreate> & {
  expected_updated_at: string;
  enabled?: boolean;
};

export interface PwPreviewRow {
  value_key: string;
  components: string[];
  events: number;
  share: number | null;
  truncated: boolean;
}

export interface PwPreview {
  mode: "PREVIEW";
  watch_id: string;
  watch_version: number;
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  searches_sent: number;
  outcome: "MEASURED" | "PARTIAL" | "NOT_MEASURED";
  reason: string | null;
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  buckets_measured: number;
  buckets_lower_bound: number;
  buckets_unmeasured: number;
  total_events: number | null;
  parsed_events: number | null;
  unparsed_events: number | null;
  unparsed_bound: "EXACT" | "UPPER_BOUND" | null;
  coverage: number | null;
  property_parses: boolean | null;
  rows_measurement: MeasurementKind | null;
  rows: PwPreviewRow[];
  queries: Array<{ search: "GROUPED" | "TOTALS"; text: string }>;
}

export interface PwValueRef {
  watch_id: string;
  watch_name: string;
  watch_version: number;
  scope_kind: PwScopeKind;
  properties: Array<{ kind: string; name: string }>;
  value_key: string;
  components: string[];
  // The tracked value's id; null once retention deleted the value row (the incident is kept).
  value_id: string | null;
}

/** `PwDetectionDetailOut`: the detector's working, projected by whitelist. A figure the detector
 *  did not record is null, never 0. The band is in events per bucket; the new-value fields are
 *  counts and the API declares no unit for them (see `newValueFigures` in `components/propertywatch/pwFormat.ts`). */
export interface PwDetectionDetail {
  reason: string | null;
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
  expected_low: number | null;
  expected_high: number | null;
  threshold: number | null;
  baseline_sample_count: number | null;
  baseline_version: number | null;
  watch_version: number | null;
  observed_events: number | null;
  expected_median: number | null;
  metric_semantics: string | null;
  observed_measurement: string | null;
  absolute_delta: number | null;
  ratio: number | null;
  ratio_basis: string | null;
  robust_score_status: string | null;
  robust_z: number | null;
  fallback_bound: number | null;
  classification: string | null;
  history_buckets_verified: number | null;
  history_buckets_from_history: number | null;
  history_floor: string | null;
  events_since_first_seen: number | null;
  buckets_with_traffic: number | null;
  claim: string | null;
  claim_code?: string | null;
  claim_params?: Record<string, unknown> | null;
}

/** A property-watch value's incident, as `GET /anomalies/{id}` serves it (PW-5c). */
export interface PwAnomalyDetail
  extends Omit<AnomalyDetail, "detection" | "subject_kind" | "pw_value"> {
  subject_kind: "PW_VALUE";
  pw_value: PwValueRef;
  detection: PwDetectionDetail | null;
}

export function isPwDetail(
  a: AnomalyDetail | WafAnomalyDetail | IpsAnomalyDetail | PwAnomalyDetail,
): a is PwAnomalyDetail {
  return a.subject_kind === "PW_VALUE";
}

export interface IpsDimensionCoverage {
  dimension: IpsDimensionName;
  // EXACT or LOWER_BOUND carry numbers; NOT_COLLECTED, FAILED and REJECTED do not.
  measurement: string;
  coverage_ratio: number | null;
  coverage_lower_bound: boolean | null;
}

export interface IpsEntityMetricBucket {
  bucket_start: string;
  bucket_seconds: number;
  // null: the source's bucket record is missing, so the origin is unknown (never LIVE).
  origin?: HistoryOrigin | null;
  events: number;
  // null when the source's bucket record is missing: unknown, not exact.
  measurement: string | null;
  coverage_ratio: number | null;
  coverage_lower_bound: boolean | null;
  admission_truncated: boolean | null;
  in_maintenance: boolean | null;
}

export interface IpsSourceMetricBucket {
  bucket_start: string;
  bucket_seconds: number;
  origin?: HistoryOrigin;
  total_events: number;
  total_records: number;
  in_maintenance: boolean;
  dimensions: IpsDimensionCoverage[];
  source_events: number | null;
  distinct_sources: number | null;
  distinct_sources_lower_bound: boolean | null;
  top1_events: number | null;
  top5_events: number | null;
  max_distinct_ports: number | null;
  blocked_events: number | null;
  allowed_events: number | null;
  unmapped_events: number | null;
}

export interface PagedIpsEntityMetrics {
  items: IpsEntityMetricBucket[];
  total: number;
  limit: number;
  offset: number;
  range_start: string;
  range_end: string;
}

export interface PagedIpsSourceMetrics {
  items: IpsSourceMetricBucket[];
  total: number;
  limit: number;
  offset: number;
  range_start: string;
  range_end: string;
}

/** One seasonal cell of an IPS baseline. Weekday is ISO (1 = Monday), the hour UTC. */
export interface IpsBaselineCell {
  metric_name: string;
  weekday: number;
  hour: number;
  median: number;
  mad: number;
  p05: number | null;
  p95: number | null;
  // Ratio and share cells of the source level only: the pooled sums behind the expectation.
  numerator_sum: number | null;
  denominator_sum: number | null;
  sample_count: number;
  is_reliable: boolean;
  completeness: number;
  baseline_version: number;
  profile_version: number;
  normalizer_version: number;
  excluded_sample_count: number;
  metric_semantics: string | null;
  window_start: string | null;
  window_end: string | null;
  computed_at: string | null;
}

export interface PagedIpsBaselines {
  items: IpsBaselineCell[];
  total: number;
  limit: number;
  offset: number;
}

export interface IpsAdmission {
  dimension: IpsDimensionName;
  admitted: number | null;
  below_admission_threshold: number | null;
  rejected_over_quota: number | null;
  untracked_entities: number | null;
  untracked_events: number | null;
  admission_truncated: boolean | null;
}

export interface IpsLatestBucket {
  bucket_start: string;
  bucket_seconds: number;
  origin?: HistoryOrigin;
  total_events: number;
  total_records: number;
  in_maintenance: boolean;
  dimensions: IpsDimensionCoverage[];
  admission: IpsAdmission[];
}

export interface IpsEntityPressure {
  dimension: IpsDimensionName;
  active: number;
  total: number;
  active_ceiling: number;
}

export interface IpsSourceBehavior {
  log_source_id: string;
  log_source_name: string;
  product_label: string | null;
  enabled: boolean;
  profile_version: number;
  validation_status: string;
  validated_profile_version: number | null;
  validated_at: string | null;
  validation_current: boolean;
  latest_bucket: IpsLatestBucket | null;
  entities: IpsEntityPressure[];
}

export interface PagedIpsSources {
  items: IpsSourceBehavior[];
  total: number;
  limit: number;
  offset: number;
}

export interface IpsEntityInventory {
  id: string;
  dimension: IpsDimensionName;
  value_key: string;
  display_name: string | null;
  first_seen_at: string;
  last_seen_at: string;
  active: boolean;
  retired_at: string | null;
  baseline: { cells_total: number; reliable_cells: number };
  // null when no detector has evaluated the entity: unevaluated is not NORMAL.
  state: AnomalyState | null;
  active_anomaly_count: number;
}

export interface PagedIpsEntities {
  items: IpsEntityInventory[];
  total: number;
  limit: number;
  offset: number;
}

// --- Phase F: custom threshold rules ---------------------------------------

export type CustomRuleSubjectKind =
  | "LOG_SOURCE"
  | "FLEET_CLASS"
  | "WAF_ENDPOINT"
  | "IPS_ENTITY"
  | "IPS_SOURCE"
  | "PW_VALUE";

export interface CustomRuleBreach {
  id: string;
  rule_id: string;
  state: string;
  first_bucket_start: string;
  last_bucket_start: string;
  opened_at: string;
  recovering_at: string | null;
  resolved_at: string | null;
  worst_value: number | null;
  worst_measurement: string | null;
  last_value: number | null;
  last_measurement: string | null;
  unmeasured_buckets: number;
  subject_label: string;
  details: Record<string, unknown>;
}

export interface CustomRule {
  id: string;
  name: string;
  description: string | null;
  subject_kind: CustomRuleSubjectKind;
  log_source_id: string | null;
  source_class: string | null;
  waf_endpoint_id: string | null;
  ips_entity_id: string | null;
  pw_value_id: string | null;
  metric: string;
  operator: string;
  threshold_value: number;
  threshold_unit: string | null;
  sustain_buckets: number;
  recovery_buckets: number;
  severity: string;
  enabled: boolean;
  consecutive_breaching: number;
  consecutive_under: number;
  last_evaluated_bucket_start: string | null;
  last_evaluated_at: string | null;
  last_status: string | null;
  last_status_reason: string | null;
  active_breach: CustomRuleBreach | null;
  alerting_mode: "shadow" | "alerting";
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export interface CustomRuleDraft {
  subject_kind: CustomRuleSubjectKind;
  log_source_id: string | null;
  source_class: string | null;
  waf_endpoint_id: string | null;
  ips_entity_id: string | null;
  pw_value_id: string | null;
  metric: string;
  operator: string;
  threshold_value: number;
  sustain_buckets: number;
  recovery_buckets: number;
  severity: string;
  enabled: boolean;
}

export interface CustomRuleCreate extends CustomRuleDraft {
  name: string;
  description: string | null;
}

export type CustomRuleUpdate = Partial<CustomRuleCreate> & {
  expected_updated_at: string;
};

export interface CustomRulePreviewBucket {
  bucket_start: string;
  value: number | null;
  measurement: "EXACT" | "LOWER_BOUND" | "NOT_MEASURED";
  completeness: string | null;
  in_maintenance: boolean;
}

export interface CustomRulePreview {
  threshold_value: number;
  unit: string;
  operator: string;
  fired_count: number;
  measured_count: number;
  unmeasured_count: number;
  buckets: CustomRulePreviewBucket[];
  breach_intervals: Array<{ start: string; end: string | null }>;
}

export interface CustomRuleCatalogMetric {
  metric: string;
  subject_kind: CustomRuleSubjectKind;
  unit: string;
  is_ratio: boolean;
  measurement: "EXACT_ONLY" | "LOWER_BOUND_PROVES_INCREASE";
  note: string;
}

export interface CustomRuleCatalog {
  metrics: CustomRuleCatalogMetric[];
  subject_kinds: Array<{
    subject_kind: CustomRuleSubjectKind;
    column: string;
    search_family?: SearchFamily;
  }>;
  // One per search family a subject kind is collected under.
  cadences?: Cadence[];
  source_classes: string[];
  operators: string[];
  evaluation_enabled: boolean;
  alerting_mode: "shadow" | "alerting";
  max_enabled: number;
  enabled_count: number;
  preview_hours_min: number;
  preview_hours_max: number;
}

type QueryValue = string | number | boolean;

function qs(params: Record<string, QueryValue | QueryValue[] | undefined>): string {
  const parts = Object.entries(params).flatMap(([key, value]) => {
    const values = Array.isArray(value) ? value : [value];
    return values
      .filter((item): item is QueryValue => item !== undefined && item !== "")
      .map((item) => `${key}=${encodeURIComponent(String(item))}`);
  });
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  logSources: () => request<LogSourceSummary[]>("/log-sources"),
  logSource: (id: string) => request<LogSourceDetail>(`/log-sources/${id}`),

  searches: (signal?: AbortSignal) => request<ScheduledSearch[]>("/searches", { signal }),
  search: (id: string, signal?: AbortSignal) =>
    request<ScheduledSearch>(`/searches/${id}`, { signal }),
  searchVersions: (id: string, signal?: AbortSignal) =>
    request<SearchVersion[]>(`/searches/${id}/versions`, { signal }),
  searchExecutions: (id: string, signal?: AbortSignal) =>
    request<SearchExecution[]>(`/searches/${id}/executions`, { signal }),
  searchResults: (
    id: string,
    metricKey = "total",
    limit = 500,
    range?: { start?: string; end?: string },
    signal?: AbortSignal,
  ) =>
    request<SearchResultTrend>(
      `/searches/${id}/results${qs({
        metric_key: metricKey,
        limit,
        start: range?.start,
        end: range?.end,
      })}`,
      { signal },
    ),
  searchMetricKeys: (id: string, limit = 1000, signal?: AbortSignal) =>
    request<MetricKeyList>(`/searches/${id}/metric-keys${qs({ limit })}`, { signal }),
  searchLatestGroups: (id: string, limit = 50, signal?: AbortSignal) =>
    request<LatestGroups>(`/searches/${id}/latest-groups${qs({ limit })}`, { signal }),
  createSearch: (definition: ScheduledSearchCreate) =>
    request<ScheduledSearch>("/searches", {
      method: "POST",
      body: JSON.stringify(definition),
    }),
  updateSearch: (id: string, definition: ScheduledSearchUpdate) =>
    request<ScheduledSearch>(`/searches/${id}`, {
      method: "PATCH",
      body: JSON.stringify(definition),
    }),
  // `enabled` alone. A patch that also carries other fields is audited as
  // search.update; this one is search.enable or search.disable (F-4).
  setSearchEnabled: (id: string, enabled: boolean) =>
    request<ScheduledSearch>(`/searches/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),

  // --- operator actions ----------------------------------------------------
  // Mutations only. Authorization is the backend's job: these call the guarded
  // endpoints and surface the status, they never pre-judge permissions here.
  runSearch: (id: string) =>
    request<SearchExecution>(`/searches/${id}/run`, { method: "POST" }),

  // Authorization is the backend's job here too: the cadence PUT and the
  // schedule PATCH are guarded endpoints, and a 403 is shown, not pre-empted.
  searchCadence: () => request<SearchCadenceItem[]>("/admin/search-cadence"),
  setSearchCadence: (family: SearchFamily, cadenceSeconds: number) =>
    request<SearchCadenceItem>(`/admin/search-cadence/${family}`, {
      method: "PUT",
      body: JSON.stringify({ cadence_seconds: cadenceSeconds }),
    }),
  historyBackfill: () => request<HistoryConsole[]>("/admin/history-backfill"),
  historyBackfillEstimate: () =>
    request<HistoryConsoleEstimate[]>("/admin/history-backfill/estimate"),
  decideHistoryBackfill: (instanceId: string, answer: HistoryAnswer) =>
    request<HistoryConsole>(`/admin/history-backfill/${instanceId}`, {
      method: "PUT",
      body: JSON.stringify({ answer }),
    }),
  startHistoryBackfill: (instanceId: string) =>
    request<HistoryConsole>(`/admin/history-backfill/${instanceId}/start`, { method: "POST" }),
  stopHistoryBackfill: (instanceId: string) =>
    request<HistoryConsole>(`/admin/history-backfill/${instanceId}/stop`, { method: "POST" }),
  setSearchSchedule: (id: string, scheduleCron: string) =>
    request<ScheduledSearch>(`/searches/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ schedule_cron: scheduleCron }),
    }),

  alerts: (params: { status?: string; limit?: number; offset?: number } = {}) =>
    request<Page<Alert>>(`/alerts${qs(params)}`),
  alert: (id: string) => request<Alert>(`/alerts/${id}`),
  alertNotifications: (id: string) => request<Notification[]>(`/alerts/${id}/notifications`),

  // --- Phase 3: offenses, rules, coverage ----------------------------------
  // All read-only. There is deliberately no offense mutation call here: the
  // backend exposes none, and QRadar stays read-only.
  offenses: (params: {
    limit?: number;
    offset?: number;
    search?: string;
    status?: string;
    sort?: string;
  } = {}) => request<Page<OffenseSummary>>(`/offenses${qs(params)}`),
  offense: (id: number) => request<OffenseDetail>(`/offenses/${id}`),
  offenseHistory: (id: number) =>
    request<OffenseHistoryPoint[]>(`/offenses/${id}/history`),
  offenseAggregates: () => request<OffenseAggregates>("/offenses/aggregates"),

  rules: (params: {
    limit?: number;
    offset?: number;
    search?: string;
    health_status?: string;
    enabled?: boolean;
  } = {}) => request<Page<RuleSummary>>(`/rules${qs(params)}`),
  rule: (id: string) => request<RuleDetail>(`/rules/${id}`),
  ruleHealthHistory: (id: string) =>
    request<RuleHealthSnapshot[]>(`/rules/${id}/health-history`),
  ruleHealthSummary: () => request<RuleHealthCount[]>("/rules/health-summary"),

  coverageSummary: () => request<CoverageSummary>("/coverage/summary"),
  coverageTechniques: (params: { limit?: number; offset?: number } = {}) =>
    request<Page<TechniqueCoverage>>(`/coverage/techniques${qs(params)}`),
  coverageByRule: () => request<RuleCoverageView[]>("/coverage/by-rule"),
  coverageByDataSource: () =>
    request<DataSourceCoverageView[]>("/coverage/by-data-source"),

  // --- Phase A: behavioral anomaly detection and investigation -------------
  // Read-only. The anomaly engine runs in the backend; nothing here triggers
  // detection, and there is no QRadar call path from the browser.
  anomalies: (params: AnomalyListParams = {}) =>
    request<Page<AnomalySummary>>(`/anomalies${qs({ ...params })}`),
  anomaly: (id: string) =>
    request<AnomalyDetail | WafAnomalyDetail | IpsAnomalyDetail | PwAnomalyDetail>(
      `/anomalies/${id}`,
    ),
  // The caller's identity and grants, for links the caller may not follow.
  caller: () => request<CallerInfo>("/me"),
  // --- Phase G: property watches. Read-only; the editor's writes are PW-8's. -------
  propertyWatches: () => request<PropertyWatch[]>("/property-watches"),
  propertyWatch: (id: string) => request<PropertyWatch>(`/property-watches/${id}`),
  propertyWatchAql: (id: string) => request<WatchAql>(`/property-watches/${id}/aql`),
  propertyWatchScope: (id: string) => request<WatchScope>(`/property-watches/${id}/scope`),
  propertyWatchCatalog: () => request<PropertyWatchCatalog>("/property-watches/catalog"),
  propertyWatchScopeOptions: (kind: PwScopeKind, query = "", signal?: AbortSignal) =>
    request<PwScopeOption[]>(
      `/property-watches/scope-options${qs({ kind, q: query || undefined, limit: 100 })}`,
      { signal },
    ),
  propertyWatchPropertyOptions: (
    kind: PwScopeKind,
    scopeId: string,
    query = "",
    signal?: AbortSignal,
  ) =>
    request<PwPropertyOptions>(
      `/property-watches/property-options${qs({
        scope_kind: kind,
        scope_id: scopeId,
        q: query || undefined,
      })}`,
      { signal },
    ),
  propertyWatchBuckets: (id: string, hours = 24) =>
    request<PwBucket[]>(`/property-watches/${id}/buckets${qs({ hours })}`),
  propertyWatchValues: (
    id: string,
    params: {
      hours?: number;
      sort?: "events" | "value" | "first_seen";
      limit?: number;
      offset?: number;
    } = {},
  ) => request<PagedPwValues>(`/property-watches/${id}/values${qs({ ...params })}`),
  // One bulk read for at most 12 values. A value is addressed by id, never by its text.
  propertyWatchSeries: (id: string, valueIds: string[], hours = 24, signal?: AbortSignal) =>
    request<PwSeries>(
      `/property-watches/${id}/series?${[
        ...valueIds.map((v) => `value_ids=${encodeURIComponent(v)}`),
        `hours=${hours}`,
      ].join("&")}`,
      { signal },
    ),
  createPropertyWatch: (definition: PropertyWatchCreate) =>
    request<PropertyWatch>("/property-watches", {
      method: "POST",
      body: JSON.stringify(definition),
    }),
  updatePropertyWatch: (id: string, definition: PropertyWatchUpdate) =>
    request<PropertyWatch>(`/property-watches/${id}`, {
      method: "PATCH",
      body: JSON.stringify(definition),
    }),
  deletePropertyWatch: (id: string) =>
    request<void>(`/property-watches/${id}`, { method: "DELETE" }),
  previewPropertyWatch: (id: string) =>
    request<PwPreview>(`/property-watches/${id}/preview`, { method: "POST" }),
  requestPropertyWatchHistory: (id: string, historyDays: number) =>
    request<PropertyWatch>(`/property-watches/${id}/history`, {
      method: "POST",
      body: JSON.stringify({ history_days: historyDays }),
    }),
  customRules: () => request<CustomRule[]>("/custom-rules"),
  customRule: (id: string) => request<CustomRule>(`/custom-rules/${id}`),
  customRuleCatalog: () => request<CustomRuleCatalog>("/custom-rules/catalog"),
  customRuleBreaches: (id: string) =>
    request<CustomRuleBreach[]>(`/custom-rules/${id}/breaches`),
  customRulePreview: (id: string, hours = 24, signal?: AbortSignal) =>
    request<CustomRulePreview>(`/custom-rules/${id}/preview${qs({ hours })}`, { signal }),
  previewCustomRule: (definition: CustomRuleDraft, hours = 24, signal?: AbortSignal) =>
    request<CustomRulePreview>(`/custom-rules/preview${qs({ hours })}`, {
      method: "POST",
      body: JSON.stringify(definition),
      signal,
    }),
  createCustomRule: (definition: CustomRuleCreate) =>
    request<CustomRule>("/custom-rules", {
      method: "POST",
      body: JSON.stringify(definition),
    }),
  updateCustomRule: (id: string, definition: CustomRuleUpdate) =>
    request<CustomRule>(`/custom-rules/${id}`, {
      method: "PATCH",
      body: JSON.stringify(definition),
    }),
  deleteCustomRule: (id: string) =>
    request<void>(`/custom-rules/${id}`, { method: "DELETE" }),
  behaviorSummary: () => request<BehaviorSummary>("/anomalies/summary"),

  sourceBehaviors: () => request<SourceBehavior[]>("/behavior/sources"),
  sourceBehavior: (id: string, params: { since?: string; until?: string } = {}) =>
    request<SourceBehavior>(`/behavior/sources/${id}${qs(params)}`),
  sourceMetrics: (id: string, params: { since?: string; until?: string; limit?: number } = {}) =>
    request<MetricBucket[]>(`/behavior/sources/${id}/metrics${qs({ ...params })}`),
  sourceBaselines: (id: string, metric = "average_eps") =>
    request<BaselineCell[]>(`/behavior/sources/${id}/baselines${qs({ metric })}`),

  // --- WAF behavior (T-057 read API) ----------------------------------------
  // Read-only. `{id}` below is the QRadar log source's id, as in the admin routes.
  wafBehaviorSources: (params: { since?: string; until?: string; limit?: number; offset?: number } = {}) =>
    request<PagedWafSources>(`/behavior/waf/sources${qs({ ...params })}`),
  wafBehaviorSource: (id: string, params: { since?: string; until?: string } = {}) =>
    request<WafSourceDetail>(`/behavior/waf/sources/${id}${qs({ ...params })}`),
  wafBehaviorEndpoints: (
    id: string,
    params: { active_only?: boolean; limit?: number; offset?: number } = {},
  ) => request<PagedWafEndpoints>(`/behavior/waf/sources/${id}/endpoints${qs({ ...params })}`),
  wafEndpointMetrics: (
    id: string,
    params: { since?: string; until?: string; limit?: number; offset?: number } = {},
  ) => request<PagedWafMetrics>(`/behavior/waf/endpoints/${id}/metrics${qs({ ...params })}`),

  wafEndpointBaselines: (
    id: string,
    params: { metric?: string; limit?: number; offset?: number } = {},
  ) => request<PagedWafBaselines>(`/behavior/waf/endpoints/${id}/baselines${qs({ ...params })}`),

  // --- IPS behavior (T-085 read API) ----------------------------------------
  // Read-only. Entity ids come from an anomaly's `ips_entity`; a source id is the
  // QRadar log source's id. No route serves a contributor list or a source IP.
  ipsEntityMetrics: (
    id: string,
    params: { since?: string; until?: string; limit?: number; offset?: number } = {},
  ) => request<PagedIpsEntityMetrics>(`/behavior/ips/entities/${id}/metrics${qs({ ...params })}`),
  ipsEntityBaselines: (
    id: string,
    params: { metric?: string; limit?: number; offset?: number } = {},
  ) => request<PagedIpsBaselines>(`/behavior/ips/entities/${id}/baselines${qs({ ...params })}`),
  ipsSourceMetrics: (
    id: string,
    params: { since?: string; until?: string; limit?: number; offset?: number } = {},
  ) => request<PagedIpsSourceMetrics>(`/behavior/ips/sources/${id}/metrics${qs({ ...params })}`),
  ipsSourceBaselines: (
    id: string,
    params: { metric?: string; limit?: number; offset?: number } = {},
  ) => request<PagedIpsBaselines>(`/behavior/ips/sources/${id}/baselines${qs({ ...params })}`),
  ipsBehaviorSources: (params: { limit?: number; offset?: number } = {}) =>
    request<PagedIpsSources>(`/behavior/ips/sources${qs({ ...params })}`),
  ipsBehaviorEntities: (
    id: string,
    params: { dimension?: IpsDimensionName; limit?: number; offset?: number } = {},
  ) => request<PagedIpsEntities>(`/behavior/ips/sources/${id}/entities${qs({ ...params })}`),

  // --- WAF source administration -------------------------------------------
  // Guarded by admin:waf. Profile writes save the application's database only;
  // validation is a request that a worker runs, never a wait on this call.
  wafMappingSchema: () => request<MappingSchemaOut>("/waf/mapping-schema"),
  wafSources: () => request<WafSourceStatus[]>("/waf/sources"),
  wafCandidates: (params: { instance_id?: string; q?: string; limit?: number } = {}) =>
    request<WafCandidateOut[]>(`/waf/candidates${qs({ ...params })}`),
  wafProfile: (logSourceId: string) =>
    request<WafProfileOut>(`/waf/log-sources/${logSourceId}/profile`),
  saveWafProfile: (logSourceId: string, body: WafProfileIn) =>
    request<WafProfileOut>(`/waf/log-sources/${logSourceId}/profile`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  validateWafProfile: (logSourceId: string) =>
    request<WafProfileOut>(`/waf/log-sources/${logSourceId}/profile/validate`, {
      method: "POST",
    }),
  enableWafProfile: (logSourceId: string) =>
    request<WafProfileOut>(`/waf/log-sources/${logSourceId}/profile/enable`, {
      method: "POST",
    }),
  disableWafProfile: (logSourceId: string) =>
    request<WafProfileOut>(`/waf/log-sources/${logSourceId}/profile/disable`, {
      method: "POST",
    }),
  wafProfileHistory: (logSourceId: string) =>
    request<WafProfileHistoryEntryOut[]>(`/waf/log-sources/${logSourceId}/profile/history`),
  wafPropertyCatalog: (logSourceId: string, refresh = false) =>
    request<PropertyCatalogOut>(
      `/waf/log-sources/${logSourceId}/property-catalog${qs({ refresh: refresh || undefined })}`,
    ),

  acknowledgeAlert: (id: string) =>
    request<Alert>(`/alerts/${id}/acknowledge`, { method: "POST" }),
  resolveAlert: (id: string, reason: string) =>
    request<Alert>(`/alerts/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
};

// Turns any thrown value into something safe to render.
//
// Never surfaces a raw exception: an unexpected error could carry a stack trace
// or an upstream URL, and this text goes straight into the DOM. Known HTTP
// statuses get an actionable message; everything else degrades to a fixed
// string.
export function actionErrorMessage(err: unknown, t?: TextTranslator): string {
  // The sentences are fixed; `t` (the interface language's translator, full keys) words them
  // from the `errors` namespace. Without it they stay English. A backend validation detail is
  // shown as the server sent it.
  const say = (key: string, english: string) => (t ? t(`errors.${key}`) : english);
  if (err instanceof ApiError) {
    switch (err.status) {
      case 401:
        return say("sessionExpired", "Your session has expired. Sign in again to continue.");
      case 403: {
        // A refusal that names the missing permission by code says which one (MSG-1); any other
        // 403 keeps the general sentence.
        const permission = missingPermission(err);
        if (permission !== null) {
          return t
            ? t("errors.forbiddenPermission", { permission })
            : `You need the ${permission} permission for this.`;
        }
        return say("forbidden", "You do not have permission to perform this action.");
      }
      case 404:
        return say("notFound", "This item no longer exists. Refresh the page.");
      case 409:
        return say("conflict", "This conflicts with the current state. Refresh and try again.");
      case 422:
        // The backend's validation detail is generated from the schema and is
        // safe to show; it tells the operator what to correct.
        return err.message || say("invalid", "The request was rejected as invalid.");
      default:
        return err.status >= 500
          ? say("server", "The server could not complete the request. Try again shortly.")
          : err.message || say("failed", "The request failed.");
    }
  }
  return say("notSent", "The request could not be sent. Check your connection and try again.");
}

// Severity → CSS pill class, shared by the views that show a severity.
//
// Severity grades how bad a finding is. It never takes the positive tone: a
// LOW finding is still a finding, and green is reserved for a genuinely good
// outcome (guidelines §10). An unrecognised value is neutral.
export function sevTone(sev: string): string {
  if (sev === "CRITICAL" || sev === "HIGH") return "crit";
  if (sev === "MEDIUM") return "warn";
  if (sev === "LOW" || sev === "INFO") return "info";
  return "";
}

// --- Phase C: log-source fleet ---------------------------------------------
// Mirrors app/schemas/fleet.py. A volume figure with no complete bucket behind
// it is `null`, never 0, so every such field is `number | null`.

export type SourceClass =
  | "WAF"
  | "IPS"
  | "FIREWALL"
  | "PROXY"
  | "VPN"
  | "LINUX"
  | "WINDOWS"
  | "NETWORK"
  | "DNS"
  | "DATABASE"
  | "EMAIL"
  | "ENDPOINT"
  | "CLOUD"
  | "IDENTITY"
  | "APPLICATION"
  | "QRADAR_INTERNAL"
  | "OTHER"
  | "UNCLASSIFIED";

export type FleetWindow = "1h" | "24h" | "7d";

export type ClassificationBasis =
  | "OVERRIDE"
  | "INTERNAL"
  | "UNCLASSIFIED"
  | "GROUP"
  | "TYPE"
  | "TYPE_NAME_REGEX"
  | "NAME_REGEX"
  | "IDENTIFIER_REGEX";

export type ClassificationReviewStatus = "NEW" | "REVIEWED";

export type ClassificationRuleKind =
  | "GROUP"
  | "TYPE"
  | "TYPE_NAME_REGEX"
  | "NAME_REGEX"
  | "IDENTIFIER_REGEX";

export interface FleetSourceCounts {
  enabled: number;
  disabled: number;
}

// `missing` is what is neither complete nor partial; the three add up to
// `expected` unless a disabled source still reports.
export interface FleetBucketCounts {
  expected: number;
  complete: number;
  // Of `complete`, the buckets read from QRadar history rather than collected by the cadence.
  complete_from_history: number;
  partial: number;
  missing: number;
}

export interface FleetTopSource {
  id: string;
  name: string;
  events: number;
}

export interface FleetClassSummary {
  source_class: SourceClass;
  sources: FleetSourceCounts;
  buckets: FleetBucketCounts;
  // Null when no complete bucket exists. Complete buckets only.
  events: number | null;
  mean_eps: number | null;
  // Null when no source in the class has a reliable baseline.
  expected_eps: number | null;
  baseline_sources: number;
  baseline_share: number | null;
  // Enabled and monitored sources: what `baseline_sources` is a share of.
  monitored_sources: number;
  // Phase A anomaly type -> open incident count.
  open_incidents: Record<string, number>;
  top_sources: FleetTopSource[];
}

export interface FleetSummary {
  window: FleetWindow;
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  classes: FleetClassSummary[];
  internal: { sources: FleetSourceCounts };
  new_sources: number;
  conflict_sources: number;
  unclassified_sources: number;
}

// One entry of `conflicts`: a lower-level candidate whose class differs from
// the winner, or a tied candidate at the winning level.
export interface ClassificationConflict {
  basis: string | null;
  rule_id: string | null;
  class: string | null;
}

export interface FleetSource {
  id: string;
  instance_id: string;
  qradar_id: number;
  name: string;
  type_name: string | null;
  enabled: boolean;
  // Null when the sync never recorded it: unknown, not "no".
  auto_discovered: boolean | null;
  first_seen_at: string;
  source_class: SourceClass;
  basis: string;
  rule_id: string | null;
  override_class: SourceClass | null;
  override_by: string | null;
  override_at: string | null;
  conflicts: ClassificationConflict[];
  classified_at: string | null;
  // Null when the source has no classification row yet.
  review_status: ClassificationReviewStatus | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

export interface FleetSourceParams {
  class?: SourceClass;
  review_status?: ClassificationReviewStatus;
  has_conflict?: boolean;
  basis?: ClassificationBasis;
  // A case-insensitive substring of the source name or its QRadar type name.
  q?: string;
  limit?: number;
  offset?: number;
}

export interface ClassificationRule {
  id: string;
  instance_id: string | null;
  kind: ClassificationRuleKind;
  pattern: string;
  target_class: SourceClass;
  priority: number;
  enabled: boolean;
  built_in: boolean;
  description: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClassificationRuleCreate {
  instance_id?: string | null;
  kind: ClassificationRuleKind;
  pattern: string;
  target_class: SourceClass;
  priority?: number;
  enabled?: boolean;
  description?: string | null;
}

export interface ClassificationRuleUpdate {
  pattern?: string;
  target_class?: SourceClass;
  priority?: number;
  enabled?: boolean;
  description?: string | null;
}

// The fleet calls differ from `request` in two ways. A 204 has no body to
// parse. And FastAPI answers a schema-validation failure (rule create) with a
// list of `{msg}` objects but an HTTPException (rule update) with a string, so
// the detail is normalised to one sentence before it reaches the DOM.
function detailText(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) =>
        d && typeof d === "object" && typeof (d as { msg?: unknown }).msg === "string"
          ? (d as { msg: string }).msg.replace(/^Value error, /, "")
          : null,
      )
      .filter((m): m is string => m !== null);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

async function fleetRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`, {
    ...init,
    // As `request`: only a request with a body carries `Content-Type`.
    headers:
      init?.body === undefined
        ? init?.headers
        : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    let raw: unknown;
    let body: unknown;
    try {
      body = await res.json();
      raw = (body as { detail?: unknown } | null)?.detail;
      detail = detailText(raw, detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, raw, body);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const fleetApi = {
  summary: (window: FleetWindow) => fleetRequest<FleetSummary>(`/fleet/summary${qs({ window })}`),
  sources: (params: FleetSourceParams = {}) =>
    fleetRequest<Page<FleetSource>>(`/fleet/sources${qs({ ...params })}`),
  // `null` clears the override. The field is required by the API: omitting it
  // is a 422, so it is always sent.
  setOverride: (id: string, overrideClass: SourceClass | null) =>
    fleetRequest<FleetSource>(`/log-sources/${id}/classification`, {
      method: "PATCH",
      body: JSON.stringify({ override_class: overrideClass }),
    }),
  markReviewed: (id: string) =>
    fleetRequest<FleetSource>(`/log-sources/${id}/review`, { method: "POST" }),
  rules: () => fleetRequest<ClassificationRule[]>("/classification-rules"),
  createRule: (body: ClassificationRuleCreate) =>
    fleetRequest<ClassificationRule>("/classification-rules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateRule: (id: string, body: ClassificationRuleUpdate) =>
    fleetRequest<ClassificationRule>(`/classification-rules/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteRule: (id: string) =>
    fleetRequest<void>(`/classification-rules/${id}`, { method: "DELETE" }),
};

// --- catch-all senders (T-069 / T-070b) ------------------------------------
// Mirrors backend/app/schemas/catchall.py. `events_24h` is a lower bound from the
// stored top lists and null when no top list named the sender: unknown, not zero.
// A bucket that was not measured has no row, so a gap in the list is a gap.
export type CatchallReviewStatus = "NEW" | "REVIEWED" | "DISMISSED";
export type CatchallMeasurement = "EXACT" | "LOWER_BOUND" | "FAILED" | "NOT_COLLECTED";

export interface CatchallSender {
  id: string;
  catchall_log_source_id: string;
  catchall_log_source_name: string;
  identifier: string;
  first_seen_at: string;
  last_seen_at: string;
  events_24h: number | null;
  active: boolean;
  review_status: CatchallReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface CatchallSenderParams {
  review_status?: CatchallReviewStatus;
  catchall_log_source_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface CatchallTopSender {
  identifier: string;
  events: number;
}

export interface CatchallBucket {
  catchall_log_source_id: string;
  bucket_start: string;
  bucket_seconds: number;
  origin?: HistoryOrigin;
  events: number;
  records: number;
  distinct_senders: number;
  distinct_senders_lower_bound: boolean;
  measurement: CatchallMeasurement;
  top_senders: CatchallTopSender[];
  admitted: number;
  below_admission_threshold: number;
  rejected_over_quota: number;
  untracked_senders: number;
  untracked_events: number;
  query_provenance: Record<string, unknown>;
  collected_at: string;
}

export interface CatchallBucketList {
  catchall_log_source_id: string;
  since: string;
  until: string;
  items: CatchallBucket[];
}

export interface CatchallSource {
  id: string;
  name: string;
  type_name: string | null;
  latest_bucket_start: string | null;
  new_sender_count: number;
}

export interface CatchallSourceList {
  catchall_type_ids: number[];
  items: CatchallSource[];
}

export const catchallApi = {
  sources: () => fleetRequest<CatchallSourceList>("/fleet/catchall/sources"),
  senders: (params: CatchallSenderParams = {}) =>
    fleetRequest<Page<CatchallSender>>(`/fleet/catchall/senders${qs({ ...params })}`),
  buckets: (sourceId: string, since: string, until: string) =>
    fleetRequest<CatchallBucketList>(
      `/fleet/catchall/buckets${qs({ catchall_log_source_id: sourceId, since, until })}`,
    ),
  review: (id: string) =>
    fleetRequest<CatchallSender>(`/fleet/catchall/senders/${id}/review`, { method: "POST" }),
  dismiss: (id: string) =>
    fleetRequest<CatchallSender>(`/fleet/catchall/senders/${id}/dismiss`, { method: "POST" }),
};

// --- outage alert policy (T-070a) ------------------------------------------
// Mirrors backend/app/schemas/outage.py. Every route needs `admin:outage`,
// reads included. `source_name` and `source_class` are null on a CLASS row and
// on a SOURCE row whose source no longer exists.
export type OutageMode = "ALERT" | "RECORD_ONLY";
export type OutageScopeKind = "CLASS" | "SOURCE";

export interface OutagePolicyRow {
  id: string;
  // Null is a global row; a value scopes the row to one QRadar instance.
  instance_id: string | null;
  scope_kind: OutageScopeKind;
  // A SourceClass for a CLASS row, a log source id for a SOURCE row.
  scope_value: string;
  mode: OutageMode;
  // Null uses the default grace.
  silence_grace_buckets: number | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
  governed_monitored_sources: number | null;
  source_name: string | null;
  source_class: SourceClass | null;
  // The instance's name on an instance-scoped row; null on a global row.
  instance_name: string | null;
}

export interface OutagePolicyUpsert {
  mode: OutageMode;
  silence_grace_buckets: number | null;
}

// The two fields T-068 added to each class of GET /fleet/summary.
export type OutageClassSummary = FleetClassSummary & {
  outage_mode: OutageMode;
  outage_overrides: number;
  // Enabled and monitored sources: the ones an outage policy governs.
  monitored_sources: number;
};
export type OutageFleetSummary = Omit<FleetSummary, "classes"> & {
  classes: OutageClassSummary[];
  // From settings: what a class with no row resolves to, and the silence grace
  // in buckets when no row overrides it.
  outage_default: OutageMode;
  default_silence_grace_buckets: number | null;
};

export const outagePolicyApi = {
  list: () => fleetRequest<OutagePolicyRow[]>("/outage-policy"),
  putClass: (sourceClass: string, body: OutagePolicyUpsert) =>
    fleetRequest<OutagePolicyRow>(`/outage-policy/classes/${encodeURIComponent(sourceClass)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteClass: (sourceClass: string, instanceId?: string | null) =>
    fleetRequest<void>(
      `/outage-policy/classes/${encodeURIComponent(sourceClass)}${qs({ instance_id: instanceId ?? undefined })}`,
      { method: "DELETE" },
    ),
  putSource: (logSourceId: string, body: OutagePolicyUpsert) =>
    fleetRequest<OutagePolicyRow>(`/outage-policy/sources/${encodeURIComponent(logSourceId)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteSource: (logSourceId: string, instanceId?: string | null) =>
    fleetRequest<void>(
      `/outage-policy/sources/${encodeURIComponent(logSourceId)}${qs({ instance_id: instanceId ?? undefined })}`,
      { method: "DELETE" },
    ),
  summary: (window: FleetWindow) =>
    fleetRequest<OutageFleetSummary>(`/fleet/summary${qs({ window })}`),
};

// --- rule statistics (T-062) -----------------------------------------------
// Mirrors backend/app/schemas/rule_statistics.py. A section the platform could
// not measure is null with a reason beside it, never an empty list.
export interface RuleCountRow {
  enabled: boolean;
  rule_type: string | null;
  origin: string | null;
  is_building_block: boolean;
  count: number;
}

export interface RuleStatisticsWindow {
  days: number;
  span_start: string;
  span_end: string;
  span_seconds: number;
  window_seconds: number;
  expected_windows: number;
  complete_windows: number;
  // Of `complete_windows`, how many were read from QRadar history.
  history_windows?: number;
  truncated_windows: number;
  failed_windows: number;
  missing_windows: number;
  measured_seconds: number;
  complete: boolean;
  reason: string | null;
  reason_code?: string | null;
  reason_params?: Record<string, unknown> | null;
}

export interface SilentRule {
  rule_id: string;
  qradar_id: number;
  name: string;
  rule_type: string | null;
  origin: string | null;
  is_building_block: boolean;
  first_seen_at: string | null;
  last_fired_at: string | null;
}

export interface SilentExclusion {
  rule_type: string | null;
  evidence: string;
  count: number;
}

export interface SilentSection {
  items: SilentRule[];
  total: number;
  items_truncated: boolean;
  excluded: SilentExclusion[];
  excluded_count: number;
  excluded_reason: string;
  excluded_reason_code?: string | null;
  excluded_reason_params?: Record<string, unknown> | null;
}

export interface MatchRankRow {
  rule_id: string;
  qradar_id: number;
  name: string;
  enabled: boolean;
  rule_type: string | null;
  is_building_block: boolean;
  match_records: number;
  matched_events: number;
  match_rate_per_second: number;
  lower_bound: boolean;
}

export interface CostRow {
  rule_id: string;
  qradar_id: number;
  name: string;
  rule_type: string | null;
  is_building_block: boolean;
  average_capacity: number;
}

export interface CostSection {
  label: string;
  label_code?: string | null;
  label_params?: Record<string, unknown> | null;
  unit: string;
  unit_code?: string | null;
  unit_params?: Record<string, unknown> | null;
  items: CostRow[];
  no_benchmark_count: number;
}

export interface RuleStatistics {
  instance_id: string;
  counts: RuleCountRow[];
  window: RuleStatisticsWindow;
  silent: SilentSection | null;
  silent_reason: string | null;
  silent_reason_code?: string | null;
  silent_reason_params?: Record<string, unknown> | null;
  top_by_matches: MatchRankRow[] | null;
  top_by_events: MatchRankRow[] | null;
  top_reason: string | null;
  top_reason_code?: string | null;
  top_reason_params?: Record<string, unknown> | null;
  costliest_by_benchmark: CostSection;
}

export interface CapacitySample {
  capacity_timestamp: string;
  average_capacity: number | null;
  base_capacity: number | null;
  base_host_id: number | null;
  recorded_at: string;
}

export const ruleStatisticsApi = {
  statistics: (days: number) => request<RuleStatistics>(`/rules/statistics${qs({ days })}`),
  capacityHistory: (id: string) => request<CapacitySample[]>(`/rules/${id}/capacity-history`),
};

// --- IPS source administration (docs/FIELD-MAPPING.md §FM.9, T-049's /ips API) ---
// Guarded by admin:ips, which admin:waf does not grant. A field reference has
// two kinds: a NORMALIZED QRadar field from the schema's allowlist, or a
// CUSTOM_PROPERTY. Kept as one block, with its calls in `ipsApi`, so the shared
// `api` object above stays untouched.

export type IpsReferenceKind = "NORMALIZED" | "CUSTOM_PROPERTY";

export interface IpsFieldReference {
  kind: IpsReferenceKind;
  name: string;
}

export type IpsPresetKey = "CISCO_FTD" | "PALO_ALTO_THREAT";

/** Logical field key to its reference; null = not mapped. */
export type IpsMapping = Record<string, IpsFieldReference | null>;

export interface IpsMappingFieldOut {
  key: string;
  label: string;
  description: string;
  required: boolean;
  used_by_collection: boolean;
  kinds: IpsReferenceKind[];
  normalized_options: string[];
  default: IpsFieldReference | null;
}

export interface IpsPresetOut {
  key: IpsPresetKey;
  label: string;
  mapping: IpsMapping;
  action_value_map: ActionValueMap;
}

export interface IpsMappingSchemaOut {
  domain: "IPS";
  property_name_pattern: string;
  min_coverage_ratio: number;
  fields: IpsMappingFieldOut[];
  value_maps: MappingValueMapOut[];
  presets: IpsPresetOut[];
}

export interface IpsProfileIn {
  /** Null only to create; otherwise the version the edit was made against. */
  expected_profile_version: number | null;
  product_label: string | null;
  /** Replaces the stored preset on every save, so an edit sends the current one. */
  preset: IpsPresetKey | null;
  mapping: IpsMapping;
  action_value_map: ActionValueMap;
}

export interface IpsValidationFieldOut {
  field: string;
  reference: IpsFieldReference | null;
  /** AVAILABLE, NOT_MAPPED, FIELD_REJECTED, UNPOPULATED, INSUFFICIENT_COVERAGE,
   *  NO_EVENTS or QUERY_FAILED. A status this build does not know is shown as recorded. */
  status: string;
  used_by_collection: boolean;
  populated_events: number | null;
  populated_ratio: number | null;
  generic_name_events: number | null;
  generic_name_ratio: number | null;
  values_truncated: boolean;
  discovered_values: DiscoveredValue[];
  detail: string | null;
  detail_code?: string | null;
  detail_params?: Record<string, unknown> | null;
  warnings: string[];
}

export interface IpsValidationReportOut {
  status: WafValidationStatus;
  reasons: string[];
  reason_codes?: (BackendMessageRef | null)[] | null;
  warnings: string[];
  total_events: number | null;
  window_start: string;
  window_end: string;
  profile_version: number;
  probe_truncated: boolean;
  ariel_searches: number;
  fields: IpsValidationFieldOut[];
}

export interface IpsProfileOut {
  profile_id: string;
  instance_id: string;
  log_source_id: string;
  qradar_log_source_id: number;
  log_source_name: string;
  log_source_type_name: string | null;
  product_label: string | null;
  preset: IpsPresetKey | null;
  profile_version: number;
  enabled: boolean;
  mapping: IpsMapping;
  action_value_map: ActionValueMap;
  validation_status: WafValidationStatus;
  validated_profile_version: number | null;
  validated_at: string | null;
  validation: IpsValidationReportOut | null;
  validation_pending: boolean;
  validation_requested_at: string | null;
  validation_requested_by: string | null;
  collection_eligible: boolean;
  ineligible_reason: string | null;
  ineligible_reason_code?: string | null;
  ineligible_reason_params?: Record<string, unknown> | null;
  updated_by: string | null;
  updated_at: string;
}

/** `GET /ips/sources`: no mapping and no per-field results, only the state. */
export interface IpsSourceStatus {
  profile_id: string;
  instance_id: string;
  log_source_id: string;
  qradar_log_source_id: number;
  log_source_name: string;
  log_source_type_name: string | null;
  product_label: string | null;
  preset: IpsPresetKey | null;
  enabled: boolean;
  profile_version: number;
  validation_status: WafValidationStatus;
  validated_profile_version: number | null;
  validated_at: string | null;
  validation_pending: boolean;
  validation_requested_at: string | null;
  validation_requested_by: string | null;
  collection_eligible: boolean;
  ineligible_reason: string | null;
  ineligible_reason_code?: string | null;
  ineligible_reason_params?: Record<string, unknown> | null;
  validation_fields?: {
    field: string;
    status: string;
    detail: string | null;
    detail_code?: string | null;
    detail_params?: Record<string, unknown> | null;
  }[];
  validation_reasons?: string[];
  validation_reason_codes?: (BackendMessageRef | null)[] | null;
  updated_by: string | null;
  updated_at: string;
}

export interface IpsCandidateOut {
  instance_id: string;
  log_source_id: string;
  qradar_id: number;
  name: string;
  type_name: string | null;
  source_class: string | null;
}

export interface IpsProfileHistoryEntryOut {
  at: string;
  actor: string | null;
  /** ips.profile.created | mapping_changed | relabelled | validation_requested |
   *  validated | enabled | disabled. Unknown values are shown as recorded. */
  action: string;
  profile_version: number | null;
  mapping_before: Record<string, unknown> | null;
  mapping_after: Record<string, unknown> | null;
  /** SUCCESS, FAILURE, SOURCE_BUSY, ... */
  outcome: string;
  reasons: string[] | null;
  reason_codes?: (BackendMessageRef | null)[] | null;
}

export const ipsApi = {
  mappingSchema: () => request<IpsMappingSchemaOut>("/ips/mapping-schema"),
  sources: () => request<IpsSourceStatus[]>("/ips/sources"),
  // instance_id is required by the route; there is no instances endpoint.
  candidates: (params: { instance_id: string; q?: string; limit?: number }) =>
    request<IpsCandidateOut[]>(`/ips/candidates${qs({ ...params })}`),
  profile: (logSourceId: string) =>
    request<IpsProfileOut>(`/ips/log-sources/${logSourceId}/profile`),
  saveProfile: (logSourceId: string, body: IpsProfileIn) =>
    request<IpsProfileOut>(`/ips/log-sources/${logSourceId}/profile`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  validateProfile: (logSourceId: string) =>
    request<IpsProfileOut>(`/ips/log-sources/${logSourceId}/profile/validate`, {
      method: "POST",
    }),
  enableProfile: (logSourceId: string) =>
    request<IpsProfileOut>(`/ips/log-sources/${logSourceId}/profile/enable`, {
      method: "POST",
    }),
  disableProfile: (logSourceId: string) =>
    request<IpsProfileOut>(`/ips/log-sources/${logSourceId}/profile/disable`, {
      method: "POST",
    }),
  profileHistory: (logSourceId: string) =>
    request<IpsProfileHistoryEntryOut[]>(`/ips/log-sources/${logSourceId}/profile/history`),
  propertyCatalog: (logSourceId: string, refresh = false) =>
    request<PropertyCatalogOut>(
      `/ips/log-sources/${logSourceId}/property-catalog${qs({ refresh: refresh || undefined })}`,
    ),
};

// --- shell top bar (UI-1.5) -------------------------------------------------
// Browser reads of existing endpoints. Each takes an AbortSignal so a superseded
// or unmounted caller can cancel; none writes anything, and none reaches QRadar.
// A refused read surfaces as an `ApiError` with its status, so the caller can
// tell "your role cannot read this" (403) from "the read failed".
export interface HealthReady {
  status: string;
  database: string;
}

export interface ApiVersion {
  version: string;
  git_sha: string | null;
  migration_head: string | null;
}

export const shellApi = {
  // Open route: it answers without a session, so the sign-in page can read it.
  version: (signal?: AbortSignal) => request<ApiVersion>("/version", { signal }),
  healthReady: (signal?: AbortSignal) => request<HealthReady>("/health/ready", { signal }),
  overview: (signal?: AbortSignal) => request<SocOverview>("/overview", { signal }),
  searchCadence: (signal?: AbortSignal) =>
    request<SearchCadenceItem[]>("/admin/search-cadence", { signal }),
  searchSources: (q: string, signal?: AbortSignal) =>
    request<Page<FleetSource>>(`/fleet/sources${qs({ q, limit: 5 })}`, { signal }),
  searchRules: (search: string, signal?: AbortSignal) =>
    request<Page<RuleSummary>>(`/rules${qs({ search, limit: 5 })}`, { signal }),
  searchOffenses: (search: string, signal?: AbortSignal) =>
    request<Page<OffenseSummary>>(`/offenses${qs({ search, limit: 5 })}`, { signal }),
  // The one bounded read the anomaly search filters client-side: the API has no
  // text search over anomalies.
  activeAnomalies: (signal?: AbortSignal) =>
    request<Page<AnomalySummary>>(`/anomalies${qs({ active_only: true, limit: 200 })}`, {
      signal,
    }),
};

// --- GET /fleet/eps-series (backend/app/schemas/fleet.py, FleetEpsSeries) --------
export type FleetSeriesGroup = "none" | "class";

/** `events` and `eps` are null unless every expected cell of the step is complete. */
export interface EpsSeriesPoint {
  step_start: string;
  events: number | null;
  eps: number | null;
  expected_eps: number | null;
  cells_complete: number;
  // Of `cells_complete`, how many were read from QRadar history.
  cells_from_history?: number;
  cells_expected: number;
  sources_expected: number;
}

export interface EpsSeries {
  key: string;
  label: string;
  sources_expected: number;
  points: EpsSeriesPoint[];
}

export interface EpsCoverage {
  steps_total: number;
  steps_measured: number;
}

export interface FleetEpsSeries {
  window: FleetWindow;
  group_by: FleetSeriesGroup;
  window_start: string;
  window_end: string;
  bucket_seconds: number;
  step_seconds: number;
  series: EpsSeries[];
  coverage: EpsCoverage;
}

// --- Home data layer (UI-2.1) ---------------------------------------------------
// Each read is a `{ key, fetch }` pair: `key` is the path with its query, which is
// the request's identity for `useApiResource`'s shared in-flight map, and `fetch`
// requests exactly that path. Existing read endpoints only; nothing writes.
export interface HomeRead<T> {
  key: string;
  fetch: (signal: AbortSignal) => Promise<T>;
}

function homeRead<T>(path: string): HomeRead<T> {
  return { key: path, fetch: (signal) => request<T>(path, { signal }) };
}

/** `GET /me`: the caller's identity and permissions (the widget catalog, UR.5.6). */
export interface CallerInfo {
  subject: string;
  email: string | null;
  /** The grants, wildcards included (`read:*`). */
  permissions: string[];
  /** Every `PERM_*` capability the caller holds once wildcards are resolved. */
  effective_permissions: string[];
  /** Present for a session. The development principal sends `local-dev`. */
  username?: string | null;
  /** `LOCAL` or `LDAP` for a session. Absent for the development principal. */
  auth_source?: string | null;
  must_change_password?: boolean;
  roles?: string[];
  /** Stored interface preference. Older backends omit it. */
  ui_language?: "en" | "tr";
}

/** Store the signed-in account's interface language. */
export function updateUiLanguage(uiLanguage: "en" | "tr") {
  return request<CallerInfo>("/me/preferences", {
    method: "PUT",
    body: JSON.stringify({ ui_language: uiLanguage }),
  });
}

// The configurable catalog widgets (UI-3.5b). A window is a preset the widget's range names;
// `since`/`until` are computed when the read runs, not when the widget renders, so a refresh
// reads the window that ends now. The read's `key` names the preset instead of the instants:
// two instants would be a new identity on every refresh, and the previous answer would leave
// the screen while the new one loads.
const WINDOW_MS: Record<FleetWindow, number> = {
  "1h": 3_600_000,
  "24h": 86_400_000,
  "7d": 604_800_000,
};

/** The instants a window covers, ending at `now` rounded down to the minute. */
export function windowBounds(window: FleetWindow, now: number = Date.now()) {
  const until = Math.floor(now / 60_000) * 60_000;
  return {
    since: new Date(until - WINDOW_MS[window]).toISOString(),
    until: new Date(until).toISOString(),
  };
}

/** The API caps a metrics page at `api_max_page_size` (200 by default). */
const WAF_METRICS_PAGE = 200;
/** 12 pages hold 7 d of five-minute buckets (2 016); a longer series is flagged truncated. */
const WAF_METRICS_MAX_PAGES = 12;
/** The source metrics route takes up to 5 000 rows in one page. */
const SOURCE_METRICS_LIMIT = 5000;
/** One metric holds at most 168 cells per normalizer version; a few pages are a bound. */
const WAF_BASELINE_MAX_PAGES = 5;

/** The stored buckets of a window, and whether the read stopped before the window's end. */
export interface BucketSeries<B> {
  buckets: B[];
  /** The number the route says exist in the window (the row count when it does not say). */
  total: number;
  truncated: boolean;
}

export const homeReads = {
  me: () => homeRead<CallerInfo>("/me"),
  fleetSummary: (window: FleetWindow) =>
    homeRead<FleetSummary>(`/fleet/summary${qs({ window })}`),
  epsSeries: (window: FleetWindow, groupBy: FleetSeriesGroup) =>
    homeRead<FleetEpsSeries>(`/fleet/eps-series${qs({ window, group_by: groupBy })}`),
  anomalySummary: () => homeRead<BehaviorSummary>("/anomalies/summary"),
  sourceBehaviors: () => homeRead<SourceBehavior[]>("/behavior/sources"),
  overview: () => homeRead<SocOverview>("/overview"),
  offenseAnalytics: () => homeRead<OffenseAnalytics>("/offenses/analytics"),
  // `total` tells "no offenses" from "no snapshot yet" (UR.4.2 card 3).
  offenseProbe: () => homeRead<Page<OffenseSummary>>(`/offenses${qs({ limit: 1 })}`),
  // The open offenses, latest update first (Home "Recent offenses").
  recentOffenses: (limit = 5) =>
    homeRead<Page<OffenseSummary>>(
      `/offenses${qs({ status: "OPEN", sort: "last_updated_time", limit })}`,
    ),
  activeAnomalies: (limit = 200) =>
    homeRead<Page<AnomalySummary>>(`/anomalies${qs({ active_only: true, limit })}`),
  anomaliesBetween: (since: string, until: string, limit = 200) =>
    homeRead<Page<AnomalySummary>>(`/anomalies${qs({ since, until, limit })}`),
  // The catalog widgets (UI-3.5a).
  coverageSummary: () => homeRead<CoverageSummary>("/coverage/summary"),
  ruleHealthSummary: () => homeRead<RuleHealthCount[]>("/rules/health-summary"),
  searchCadence: () => homeRead<SearchCadenceItem[]>("/admin/search-cadence"),
  // The queue reads NEW first, then busiest; the widget re-orders the page it gets.
  newCatchallSenders: (limit = 50) =>
    homeRead<Page<CatchallSender>>(
      `/fleet/catchall/senders${qs({ review_status: "NEW", limit })}`,
    ),
  // The same path as `fleetSummary`, typed with the outage fields the route also returns.
  outageSummary: (window: FleetWindow) =>
    homeRead<OutageFleetSummary>(`/fleet/summary${qs({ window })}`),
  // The API caps a page at 200 sources.
  wafSources: (limit = 200) => homeRead<PagedWafSources>(`/behavior/waf/sources${qs({ limit })}`),
  activeWafAnomalies: (limit = 5) =>
    homeRead<Page<AnomalySummary>>(
      `/anomalies${qs({ subject_kind: "WAF_ENDPOINT", active_only: true, limit })}`,
    ),
  // The latest active anomalies of one IPS subject kind (UI-3.5b); the widget merges the two.
  activeIpsAnomalies: (kind: "IPS_ENTITY" | "IPS_SOURCE", limit = 5) =>
    homeRead<Page<AnomalySummary>>(
      `/anomalies${qs({ subject_kind: kind, active_only: true, limit })}`,
    ),
  // One WAF source's endpoints (the API caps a page at 200; `total` says if more exist).
  wafEndpointsOf: (logSourceId: string, limit = 200) =>
    homeRead<PagedWafEndpoints>(`/behavior/waf/sources/${logSourceId}/endpoints${qs({ limit })}`),
  wafEndpointSeries: (endpointId: string, window: FleetWindow): HomeRead<BucketSeries<WafMetricBucket>> => ({
    key: `/behavior/waf/endpoints/${endpointId}/metrics?window=${window}`,
    fetch: async (signal) => {
      const { since, until } = windowBounds(window);
      const buckets: WafMetricBucket[] = [];
      let total = 0;
      for (let page = 0; page < WAF_METRICS_MAX_PAGES; page += 1) {
        const got = await request<PagedWafMetrics>(
          `/behavior/waf/endpoints/${endpointId}/metrics${qs({
            since,
            until,
            limit: WAF_METRICS_PAGE,
            offset: buckets.length,
          })}`,
          { signal },
        );
        total = got.total;
        buckets.push(...got.items);
        if (got.items.length === 0 || buckets.length >= total) break;
      }
      return { buckets, total, truncated: buckets.length < total };
    },
  }),
  wafEndpointCells: (endpointId: string): HomeRead<WafBaselineCell[]> => ({
    key: `/behavior/waf/endpoints/${endpointId}/baselines?metric=request_count`,
    fetch: async (signal) => {
      const cells: WafBaselineCell[] = [];
      for (let page = 0; page < WAF_BASELINE_MAX_PAGES; page += 1) {
        const got = await request<PagedWafBaselines>(
          `/behavior/waf/endpoints/${endpointId}/baselines${qs({
            metric: "request_count",
            limit: 200,
            offset: cells.length,
          })}`,
          { signal },
        );
        cells.push(...got.items);
        if (got.items.length === 0 || cells.length >= got.total) break;
      }
      return cells;
    },
  }),
  sourceSeries: (sourceId: string, window: FleetWindow): HomeRead<BucketSeries<MetricBucket>> => ({
    key: `/behavior/sources/${sourceId}/metrics?window=${window}`,
    fetch: async (signal) => {
      const { since, until } = windowBounds(window);
      const buckets = await request<MetricBucket[]>(
        `/behavior/sources/${sourceId}/metrics${qs({ since, until, limit: SOURCE_METRICS_LIMIT })}`,
        { signal },
      );
      // The route reports no total: a full page may have more behind it.
      return {
        buckets,
        total: buckets.length,
        truncated: buckets.length >= SOURCE_METRICS_LIMIT,
      };
    },
  }),
  sourceCells: (sourceId: string) =>
    homeRead<BaselineCell[]>(`/behavior/sources/${sourceId}/baselines${qs({ metric: "average_eps" })}`),
};
