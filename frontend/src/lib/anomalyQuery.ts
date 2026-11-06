// Parsing the anomaly list's query string.
//
// Query parameters are user-controlled input arriving from a URL, so nothing
// here trusts them. Enum-valued filters are checked against an allow-list
// rather than forwarded, so a crafted value cannot be reflected back into the
// page or smuggled into the upstream request; timestamps are validated and the
// range is clamped before it reaches an endpoint that queries a hypertable.

import { isIpsType } from "@/components/ips/ipsLabels";
import { isPwType } from "@/components/propertywatch/pwFormat";
import { isWafType } from "@/components/waf/wafLabels";

import type { SubjectKind } from "./api";
import { ENGLISH_ANOMALIES } from "./anomaliesI18n";
import type { BehaviorI18n } from "./behaviorI18n";
import { MAX_RANGE_DAYS, parseRange, rangeQuery, type RangeState } from "./timeRange";

export { MAX_RANGE_DAYS };

export const PAGE_SIZE = 25;

export type SearchParamValue = string | string[] | undefined;

const DETECTOR_TYPES = new Set(["VOLUME_SPIKE", "VOLUME_DROP", "NO_EVENTS"]);
const STATES = new Set([
  "INSUFFICIENT_DATA",
  "NORMAL",
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
]);
const SEVERITIES = new Set(["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]);
const EVIDENCE = new Set([
  "NOT_REQUESTED",
  "PENDING",
  "COMPLETE",
  "PARTIAL",
  "UNAVAILABLE",
  "FAILED",
]);
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface AnomalyQuery {
  offset: number;
  active_only: boolean;
  log_source_id: string;
  instance_id: string;
  anomaly_type: string;
  state: string;
  severity: string;
  evidence_status: string;
  /** ISO-8601, or "" when unset. Sent to the API. */
  since: string;
  until: string;
  /** The time range as the URL asked for it: a relative chip or a Custom window (SOC-7b). */
  range: RangeState;
  /** Set when a supplied range was corrected or dropped, so the page can say so. */
  rangeNote: string | null;
}

function first(value: SearchParamValue): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function pick(value: SearchParamValue, allowed: Set<string>): string {
  value = first(value);
  return value && allowed.has(value) ? value : "";
}

function uuid(value: SearchParamValue): string {
  value = first(value);
  return value && UUID_RE.test(value) ? value : "";
}

export function parseAnomalyQuery(
  params: Record<string, SearchParamValue>,
  i18n: BehaviorI18n = ENGLISH_ANOMALIES,
  nowMs: number = Date.now(),
): AnomalyQuery {
  // Negative or non-numeric offsets become 0 rather than being forwarded; the
  // backend rejects them, but a rejected page is a worse answer than page one.
  const parsedOffset = Number(first(params.offset) ?? 0);
  const offset =
    Number.isFinite(parsedOffset) && parsedOffset > 0
      ? Math.floor(parsedOffset)
      : 0;

  const range = parseRange(params, nowMs);
  const rangeNote = range.issues.length
    ? range.issues.map((issue) => i18n.t(`anomalies.rangeNote.${issue}`, { days: MAX_RANGE_DAYS })).join(" ")
    : null;

  return {
    offset,
    active_only: first(params.active_only) === "true",
    log_source_id: uuid(params.log_source_id),
    instance_id: uuid(params.instance_id),
    anomaly_type: pick(params.anomaly_type, DETECTOR_TYPES),
    state: pick(params.state, STATES),
    severity: pick(params.severity, SEVERITIES),
    evidence_status: pick(params.evidence_status, EVIDENCE),
    since: range.since,
    until: range.until,
    range,
    rangeNote,
  };
}

/** The subset of a parsed query that is preserved across pagination links. */
export function paginationParams(
  q: AnomalyQuery,
): Record<string, string | undefined> {
  return {
    active_only: q.active_only ? "true" : undefined,
    log_source_id: q.log_source_id || undefined,
    instance_id: q.instance_id || undefined,
    anomaly_type: q.anomaly_type || undefined,
    state: q.state || undefined,
    severity: q.severity || undefined,
    evidence_status: q.evidence_status || undefined,
    ...rangeQuery(q.range),
  };
}

// --- subject filters -------------------------------------------------------
// Parsed beside the Phase A query, not inside it: a WAF detector type is a valid
// `anomaly_type` on the list and the Phase A allow-list above would drop it. The
// same rule applies, an allow-list, so a crafted value is dropped rather than
// forwarded upstream or reflected back into the page.

export type SubjectFilterKind = SubjectKind | "IPS";

export const SUBJECT_KINDS: Array<{ value: SubjectFilterKind; label: string }> = [
  { value: "LOG_SOURCE", label: "Log sources" },
  { value: "WAF_ENDPOINT", label: "WAF endpoints" },
  { value: "IPS_ENTITY", label: "IPS entities" },
  { value: "IPS_SOURCE", label: "IPS sources" },
  { value: "IPS", label: "IPS entities and sources" },
  { value: "PW_VALUE", label: "Property watch values" },
];

export interface SubjectQuery {
  /** "" when unset: both kinds are listed. */
  subject_kind: SubjectFilterKind | "";
  /** A WAF detector type, or "" (Phase A types are parsed by `parseAnomalyQuery`). */
  waf_anomaly_type: string;
  /** An IPS detector type, or "". */
  ips_anomaly_type: string;
  /** A property-watch detector type, or "". */
  pw_anomaly_type: string;
  /** An IPS entity id (a link from an entity view), or "" when absent or not a UUID. */
  ips_entity_id: string;
  /** One property watch (a link from its page), or "" when absent or not a UUID. */
  property_watch_id: string;
}

export function parseSubjectQuery(params: Record<string, SearchParamValue>): SubjectQuery {
  const rawKind = first(params.subject_kind);
  const kind = SUBJECT_KINDS.find((k) => k.value === rawKind)?.value ?? "";
  const type = first(params.anomaly_type);
  return {
    subject_kind: kind,
    waf_anomaly_type: type && isWafType(type) ? type : "",
    ips_anomaly_type: type && isIpsType(type) ? type : "",
    pw_anomaly_type: type && isPwType(type) ? type : "",
    ips_entity_id: uuid(params.ips_entity_id),
    property_watch_id: uuid(params.property_watch_id),
  };
}

export function subjectKindLabel(value: string, i18n: BehaviorI18n = ENGLISH_ANOMALIES): string {
  return SUBJECT_KINDS.some((k) => k.value === value)
    ? i18n.t(`anomalies.subjectKind.${value}`)
    : value;
}

/** Translate the URL-only all-IPS value into the repeated API query values. */
export function subjectKindsForApi(
  value: SubjectFilterKind | "",
): SubjectKind | SubjectKind[] | undefined {
  if (value === "IPS") return ["IPS_ENTITY", "IPS_SOURCE"];
  return value || undefined;
}
