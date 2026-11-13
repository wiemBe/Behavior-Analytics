// Baseline training exclusions (SOC-13d): the arithmetic and the words behind the control on a
// resolved Phase A anomaly and the list on the source page. Everything here is pure.
//
// The server decides everything that matters (subject, scope, lifecycle, overlap, cap). This module
// only proposes the default interval the way the server measures it, refuses a narrowing before it
// is sent, and parses the UTC text the analyst types. A coded refusal from the server is shown as it
// came; nothing here rewords it.

import type { AnomalyDetail, AnomalyState, ApiError, MetricBucket, TrainingExclusion } from "@/lib/api";
import { missingPermission } from "@/lib/refusal";

export const TRAIN_PERMISSION = "anomaly:triage";
export const REASON_MAX = 1000;
export const MAX_DAYS = 7;
export const MAX_MS = MAX_DAYS * 86_400_000;
/** The only baseline scope the first release writes (the server's `LOG_SOURCE_VOLUME_SCOPE`). */
export const VOLUME_SCOPE = "log_source:volume";
export const SOURCE_SUBJECT = "log_source";

/** The exact grant, or the administrator wildcard. */
export function canExclude(permissions: readonly string[]): boolean {
  return permissions.includes(TRAIN_PERMISSION) || permissions.includes("admin:*");
}

export type Availability =
  /** A resolved Phase A anomaly: the control may be offered. */
  | "available"
  /** Still running (candidate, open, recovering): "Available once the anomaly is resolved." */
  | "active"
  /** Phase A but not resolved and not running (normal, suppressed, insufficient data). */
  | "not-resolved"
  /** Another family: no control, no line. */
  | "unsupported";

const RUNNING: readonly AnomalyState[] = ["CANDIDATE", "OPEN", "RECOVERING"];

export function availability(
  anomaly: Pick<AnomalyDetail, "state" | "subject_kind">,
): Availability {
  if ((anomaly.subject_kind ?? "LOG_SOURCE") !== "LOG_SOURCE") return "unsupported";
  if (anomaly.state === "RESOLVED") return "available";
  return RUNNING.includes(anomaly.state) ? "active" : "not-resolved";
}

// --- the families (SOC-13f) -------------------------------------------------------------------
// The server derives the subject and the scope of every family from the anomaly; the client only
// needs to know which anomalies may carry the control, which path segment to post to, which
// subject to list, and which words name the baseline. Everything else is the server's.

/** Which baseline an exclusion touches; it picks the words, not the behaviour. */
export type Variant =
  | "source"
  | "waf"
  | "ipsEntity"
  | "ipsSource"
  | "pwValue"
  | "ruleStorm"
  | "ruleSilence";

export interface ExclusionTarget {
  /** The server's path segment: `log_source`, `waf`, `ips`, `property_watch` or `rule`. */
  family: string;
  variant: Variant;
  /** The subject the exclusion is stored against (what the list endpoint filters by). */
  subjectKind: string;
  subjectId: string;
  /** The baseline scope when exactly one exists; `undefined` for a rule (storm and silence). */
  scopeKey?: string;
}

export const WAF_SCOPE = "waf:endpoint";
export const IPS_ENTITY_SCOPE = "ips:entity";
export const IPS_SOURCE_SCOPE = "ips:source";
export const PW_SCOPE = "pw:value";
export const RULE_STORM_SCOPE = "rule:storm";
export const RULE_SILENCE_SCOPE = "rule:silence";

/** Only the fields the decision reads; an `AnomalyDetail` of any family fits. */
export interface TargetFields {
  subject_kind?: string;
  anomaly_type?: string;
  log_source_id?: string | null;
  waf_endpoint?: { id: string } | null;
  ips_entity?: { id: string } | null;
  pw_value?: { value_id: string | null } | null;
  rule?: { rule_id: string } | null;
}

// A novelty detector (a new property value or
// combination, a new endpoint, a new destination port) has no band of its own: the server refuses
// the property-watch ones, and for the other two the exclusion would change a baseline that the
// detector which raised the anomaly never reads, so no control is offered.
const IPS_SOURCE_TYPES: readonly string[] = [
  "IPS_SOURCE_CONCENTRATION_SPIKE",
  "IPS_PORT_FANOUT_SPIKE",
  "IPS_BLOCK_RATIO_SHIFT",
];
const NOVELTY: readonly string[] = [
  "WAF_NEW_ENDPOINT_ACCESS",
  "IPS_NEW_DESTINATION_PORT",
  "PW_NEW_VALUE",
  "PW_NEW_COMBINATION",
];

/**
 * Where an anomaly's exclusion goes, or `null` when the anomaly cannot carry one (another detector
 * type, a novelty detector, a subject the read did not name). A null is "no control", never a
 * disabled button.
 */
export function exclusionTargetOf(a: TargetFields): ExclusionTarget | null {
  const kind = a.subject_kind ?? "LOG_SOURCE";
  const type = a.anomaly_type ?? "";
  switch (kind) {
    case "LOG_SOURCE":
      return {
        family: "log_source",
        variant: "source",
        subjectKind: SOURCE_SUBJECT,
        subjectId: a.log_source_id ?? "",
        scopeKey: VOLUME_SCOPE,
      };
    case "WAF_ENDPOINT":
      return a.waf_endpoint && !NOVELTY.includes(type)
        ? { family: "waf", variant: "waf", subjectKind: "endpoint", subjectId: a.waf_endpoint.id, scopeKey: WAF_SCOPE }
        : null;
    case "IPS_ENTITY":
      return a.ips_entity && !NOVELTY.includes(type)
        ? { family: "ips", variant: "ipsEntity", subjectKind: "entity", subjectId: a.ips_entity.id, scopeKey: IPS_ENTITY_SCOPE }
        : null;
    case "IPS_SOURCE":
      return a.log_source_id && IPS_SOURCE_TYPES.includes(type)
        ? { family: "ips", variant: "ipsSource", subjectKind: "source", subjectId: a.log_source_id, scopeKey: IPS_SOURCE_SCOPE }
        : null;
    case "PW_VALUE":
      return a.pw_value?.value_id && !NOVELTY.includes(type)
        ? { family: "property_watch", variant: "pwValue", subjectKind: "value", subjectId: a.pw_value.value_id, scopeKey: PW_SCOPE }
        : null;
    case "RULE":
      if (!a.rule || (type !== "RULE_STORM" && type !== "RULE_SILENCE")) return null;
      return {
        family: "rule",
        variant: type === "RULE_STORM" ? "ruleStorm" : "ruleSilence",
        subjectKind: "rule",
        subjectId: a.rule.rule_id,
        scopeKey: type === "RULE_STORM" ? RULE_STORM_SCOPE : RULE_SILENCE_SCOPE,
      };
    default:
      return null;
  }
}

/** Availability across families: `unsupported` unless {@link exclusionTargetOf} names a target. */
export function exclusionAvailability(
  anomaly: Pick<AnomalyDetail, "state"> & TargetFields,
): Availability {
  if (exclusionTargetOf(anomaly) === null) return "unsupported";
  if (anomaly.state === "RESOLVED") return "available";
  return RUNNING.includes(anomaly.state) ? "active" : "not-resolved";
}

/** A rule has no per-rule rebuild stamp, so its exclusion is never reported as applied. */
export const isRuleRow = (row: Pick<TrainingExclusion, "subject_kind">) => row.subject_kind === "rule";

/** The message key of a row's effect state: a rule row that is pending reads "used from the next evaluation". */
export function stateKey(row: Pick<TrainingExclusion, "state" | "subject_kind">): string {
  return isRuleRow(row) && row.state === "PENDING_REBUILD" ? "stateRule.PENDING_REBUILD" : `state.${row.state}`;
}

/** The message key of the sentence shown after a create, by the row the server returned. */
export function resultKey(row: Pick<TrainingExclusion, "state" | "subject_kind">): string {
  return isRuleRow(row) && row.state === "PENDING_REBUILD" ? "resultRule.PENDING_REBUILD" : `result.${row.state}`;
}

/**
 * The bucket width of a family's own series: the latest LIVE bucket at or before `detected_at`,
 * as the server reads it (a history bucket carries the replay's width). A bucket with no origin
 * on an older response counts as live; a bucket whose origin is unknown (`null`) does not.
 */
export function liveBucketWidthFor(
  anomaly: Pick<AnomalyDetail, "detected_at">,
  buckets: readonly { bucket_start: string; bucket_seconds: number; origin?: string | null }[] | null,
): number | null {
  if (!buckets) return null;
  return bucketWidthFor(
    { detected_at: anomaly.detected_at, detection: null },
    buckets.filter((b) => b.origin === undefined || b.origin === "LIVE"),
  );
}

/**
 * The bucket width the server will align to: its `log_source_metric` row at or before
 * `detected_at` (the latest one), else the width the detection recorded. `null` when neither is
 * known; the control then says so instead of guessing a width.
 */
export function bucketWidthFor(
  anomaly: Pick<AnomalyDetail, "detected_at" | "detection">,
  buckets: readonly Pick<MetricBucket, "bucket_start" | "bucket_seconds">[],
): number | null {
  const detected = Date.parse(anomaly.detected_at);
  let best: { at: number; seconds: number } | null = null;
  for (const b of buckets) {
    const at = Date.parse(b.bucket_start);
    if (!Number.isFinite(at) || at > detected || !(b.bucket_seconds > 0)) continue;
    if (best === null || at > best.at) best = { at, seconds: b.bucket_seconds };
  }
  if (best) return best.seconds;
  const recorded = anomaly.detection?.bucket_seconds;
  return recorded != null && recorded > 0 ? recorded : null;
}

export interface Span {
  startMs: number;
  endMs: number;
}

/**
 * The incident span the server protects automatically: from `detected_at` to the resolution
 * (`resolved_at`, else `anomaly_end`, else `detected_at`), exactly as the server measures it.
 * Returns the default interval aligned outward to the bucket width (so it always covers the
 * incident, and is never empty), or `null` for an unreadable time.
 */
export function defaultInterval(
  anomaly: Pick<AnomalyDetail, "detected_at" | "resolved_at" | "anomaly_end">,
  bucketSeconds: number,
): Span | null {
  const width = bucketSeconds * 1000;
  const start = Date.parse(anomaly.detected_at);
  const end = Date.parse(anomaly.resolved_at ?? anomaly.anomaly_end ?? anomaly.detected_at);
  if (!(width > 0) || !Number.isFinite(start) || !Number.isFinite(end)) return null;
  const startMs = Math.floor(start / width) * width;
  const endMs = Math.max(Math.ceil(end / width) * width, startMs + width);
  return { startMs, endMs };
}

const pad = (n: number) => String(n).padStart(2, "0");

/** `2026-11-04 08:00`: the text the fields hold (the zone is named beside them, always UTC). */
export function utcFieldText(ms: number): string {
  const d = new Date(ms);
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  );
}

/** Parse `YYYY-MM-DD HH:MM` (or with a `T`), read as UTC. `null` for anything else. */
export function parseUtcField(text: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/.exec(text.trim());
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number);
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59) return null;
  const ms = Date.UTC(y, mo - 1, d, h, mi);
  const back = new Date(ms);
  // Date.UTC rolls 31 February over to March: refuse a day the month does not have.
  return back.getUTCMonth() === mo - 1 && back.getUTCDate() === d ? ms : null;
}

/** An interval as `04 Nov 08:00–10:00 UTC`, or with both dates when it crosses midnight. */
export function intervalText(startIso: string, endIso: string): string {
  const s = Date.parse(startIso);
  const e = Date.parse(endIso);
  if (!Number.isFinite(s) || !Number.isFinite(e)) return "—";
  const date = (ms: number) => {
    const d = new Date(ms);
    return `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]}`;
  };
  const clock = (ms: number) => {
    const d = new Date(ms);
    return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  };
  return date(s) === date(e)
    ? `${date(s)} ${clock(s)}–${clock(e)} UTC`
    : `${date(s)} ${clock(s)} – ${date(e)} ${clock(e)} UTC`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export type IntervalProblem =
  | "start-unreadable"
  | "end-unreadable"
  | "unaligned"
  | "start-order"
  | "start-later"
  | "end-earlier"
  | "too-long"
  | "not-widened";

/**
 * What is wrong with the typed interval, or `null`. Order matters: an unreadable field first, then
 * the bucket alignment, then the narrowing, then the cap, then "nothing added". The server checks
 * all of it again; this only saves the round trip.
 */
export function intervalProblem(
  startText: string,
  endText: string,
  base: Span,
  bucketSeconds: number,
): IntervalProblem | null {
  const start = parseUtcField(startText);
  const end = parseUtcField(endText);
  if (start === null) return "start-unreadable";
  if (end === null) return "end-unreadable";
  const width = bucketSeconds * 1000;
  if (start % width !== 0 || end % width !== 0) return "unaligned";
  if (start >= end) return "start-order";
  if (start > base.startMs) return "start-later";
  if (end < base.endMs) return "end-earlier";
  if (end - start > MAX_MS) return "too-long";
  if (start === base.startMs && end === base.endMs) return "not-widened";
  return null;
}

/** Rows that still matter to this baseline: not revoked. */
export const isLive = (row: Pick<TrainingExclusion, "state">) => row.state !== "REVOKED";

/** Rows the chart shades: an effect is pending or applied (a row outside the lookback changes nothing). */
export const isEffective = (row: Pick<TrainingExclusion, "state">) =>
  row.state === "PENDING_REBUILD" || row.state === "APPLIED";

/** The permission a 403 names, by code or by the sentence "missing required permission: x". */
export function permissionOf(err: ApiError): string | null {
  const coded = missingPermission(err);
  if (coded) return coded;
  const named = /missing required permission:\s*([a-z_]+:[a-z_*]+)/.exec(err.message);
  return named ? named[1] : null;
}
