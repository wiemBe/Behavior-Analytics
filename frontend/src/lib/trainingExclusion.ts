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
