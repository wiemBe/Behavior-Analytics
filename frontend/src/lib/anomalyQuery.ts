// Parsing the anomaly list's query string.
//
// Query parameters are user-controlled input arriving from a URL, so nothing
// here trusts them. Enum-valued filters are checked against an allow-list
// rather than forwarded, so a crafted value cannot be reflected back into the
// page or smuggled into the upstream request; timestamps are validated and the
// range is clamped before it reaches an endpoint that queries a hypertable.

import { isWafType } from "@/components/waf/wafLabels";

import type { SubjectKind } from "./api";

export const PAGE_SIZE = 25;

/** Mirrors the backend's `api_max_range_days` guard, applied before the call
 *  rather than after, so an over-wide range is corrected instead of 422ing. */
export const MAX_RANGE_DAYS = 365;

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
  /** Set when a supplied range was corrected, so the page can say so. */
  rangeNote: string | null;
}

function pick(value: string | undefined, allowed: Set<string>): string {
  return value && allowed.has(value) ? value : "";
}

function uuid(value: string | undefined): string {
  return value && UUID_RE.test(value) ? value : "";
}

/**
 * `datetime-local` value → ISO instant, or "" if it is not a real timestamp.
 *
 * A value with no zone designator is read as UTC, which is the timezone the
 * filter's labels and every timestamp on these pages state. Left bare,
 * `Date.parse` would read it in the server's local zone.
 */
function instant(value: string | undefined): string {
  if (!value) return "";
  const zoned = /(Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
  const ms = Date.parse(zoned);
  return Number.isNaN(ms) ? "" : new Date(ms).toISOString();
}

export function parseAnomalyQuery(
  params: Record<string, string | undefined>,
): AnomalyQuery {
  // Negative or non-numeric offsets become 0 rather than being forwarded; the
  // backend rejects them, but a rejected page is a worse answer than page one.
  const parsedOffset = Number(params.offset ?? 0);
  const offset =
    Number.isFinite(parsedOffset) && parsedOffset > 0
      ? Math.floor(parsedOffset)
      : 0;

  let since = instant(params.since);
  let until = instant(params.until);
  let rangeNote: string | null = null;

  // An inverted range is a mistake, not an attack, and swapping it is what the
  // operator meant. Sending it through would only produce a 422.
  if (since && until && Date.parse(since) > Date.parse(until)) {
    [since, until] = [until, since];
    rangeNote = "The start and end times were inverted and have been swapped.";
  }

  if (since && until) {
    const span = Date.parse(until) - Date.parse(since);
    const max = MAX_RANGE_DAYS * 86400_000;
    if (span > max) {
      since = new Date(Date.parse(until) - max).toISOString();
      rangeNote = `The requested range exceeded ${MAX_RANGE_DAYS} days and was clamped.`;
    }
  }

  return {
    offset,
    active_only: params.active_only === "true",
    log_source_id: uuid(params.log_source_id),
    instance_id: uuid(params.instance_id),
    anomaly_type: pick(params.anomaly_type, DETECTOR_TYPES),
    state: pick(params.state, STATES),
    severity: pick(params.severity, SEVERITIES),
    evidence_status: pick(params.evidence_status, EVIDENCE),
    since,
    until,
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
    since: q.since || undefined,
    until: q.until || undefined,
  };
}

// --- subject filters -------------------------------------------------------
// Parsed beside the Phase A query, not inside it: a WAF detector type is a valid
// `anomaly_type` on the list and the Phase A allow-list above would drop it. The
// same rule applies, an allow-list, so a crafted value is dropped rather than
// forwarded upstream or reflected back into the page.

export const SUBJECT_KINDS: Array<{ value: SubjectKind; label: string }> = [
  { value: "LOG_SOURCE", label: "Log sources" },
  { value: "WAF_ENDPOINT", label: "WAF endpoints" },
];

export interface SubjectQuery {
  /** "" when unset: both kinds are listed. */
  subject_kind: SubjectKind | "";
  /** A WAF detector type, or "" (Phase A types are parsed by `parseAnomalyQuery`). */
  waf_anomaly_type: string;
}

export function parseSubjectQuery(params: Record<string, string | undefined>): SubjectQuery {
  const kind = SUBJECT_KINDS.find((k) => k.value === params.subject_kind)?.value ?? "";
  const type = params.anomaly_type;
  return {
    subject_kind: kind,
    waf_anomaly_type: type && isWafType(type) ? type : "",
  };
}

export function subjectKindLabel(value: string): string {
  return SUBJECT_KINDS.find((k) => k.value === value)?.label ?? value;
}
