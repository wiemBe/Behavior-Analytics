// How WAF subjects and detectors are named on screen.
//
// An analyst reads "Request-rate spike", never `WAF_REQUEST_RATE_SPIKE`, and a
// WAF row is identified by what it is about -- `GET /portal/login` on a named
// source -- not by a bare log source name. These live beside the WAF components
// rather than in `lib/behavior.ts` so the Phase A vocabulary stays untouched;
// `anomalyTypeLabel` falls through to it for every Phase A type.

import type { AnomalySummary, MeasurementKind, WafEndpointRef } from "@/lib/api";
import { detectorLabel } from "@/lib/behavior";

const WAF_TYPE_LABELS: Record<string, string> = {
  WAF_REQUEST_RATE_SPIKE: "Request-rate spike",
  WAF_CLIENT_CONCENTRATION_SPIKE: "Client concentration spike",
  WAF_BLOCK_RATIO_SPIKE: "Block-ratio spike",
  WAF_ERROR_RATIO_SPIKE: "Server-error ratio spike",
  WAF_CLIENT_ERROR_RATIO_SPIKE: "Client-error ratio spike",
  WAF_NEW_ENDPOINT_ACCESS: "New endpoint access",
  WAF_CLIENT_ENDPOINT_FANOUT_SPIKE: "Client endpoint fan-out spike",
};

/** The WAF detector types the list filter offers, in the order shown. */
export const WAF_TYPES = Object.keys(WAF_TYPE_LABELS);

export function isWafType(type: string | null | undefined): boolean {
  return type != null && type in WAF_TYPE_LABELS;
}

/** A detector's name for display, Phase A or WAF. Unknown types show as sent. */
export function anomalyTypeLabel(type: string): string {
  return WAF_TYPE_LABELS[type] ?? detectorLabel(type);
}

/** `GET /portal/login`: the method and the normalized path, nothing else. */
export function endpointLabel(ref: Pick<WafEndpointRef, "http_method" | "normalized_endpoint">) {
  return `${ref.http_method} ${ref.normalized_endpoint}`;
}

export function isWafRow(a: Pick<AnomalySummary, "subject_kind" | "waf_endpoint">): boolean {
  return a.subject_kind === "WAF_ENDPOINT" && a.waf_endpoint != null;
}

/**
 * The text identifying what a list row is about.
 *
 * A WAF row reads `GET /portal/login · lab-waf-behavior-01`; a Phase A row is
 * its source name. A source whose row is gone falls back to the id so the cell
 * is never blank.
 */
export function subjectText(a: AnomalySummary): string {
  const source = a.log_source_name ?? a.log_source_id;
  return a.waf_endpoint && isWafRow(a) ? `${endpointLabel(a.waf_endpoint)} · ${source}` : source;
}

export function isLowerBound(m: MeasurementKind | string | null | undefined): boolean {
  return m === "LOWER_BOUND";
}

const STATE_LABELS: Record<string, string> = {
  INSUFFICIENT_DATA: "Insufficient data",
  NORMAL: "Normal",
  CANDIDATE: "Candidate",
  OPEN: "Open",
  RECOVERING: "Recovering",
  RESOLVED: "Resolved",
  SUPPRESSED: "Suppressed",
};

/** A lifecycle state as a word: "Insufficient data", never `INSUFFICIENT_DATA`. */
export function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

/**
 * A lifecycle state as the state pill shows it: the backend's own name, in upper case
 * ("OPEN", "INSUFFICIENT_DATA"). The anomaly list renders that string as it arrives; this is
 * the same string for every investigation pill.
 */
export function statePillText(state: string): string {
  return state.toUpperCase();
}

const EVIDENCE_LABELS: Record<string, string> = {
  NOT_REQUESTED: "Not requested",
  PENDING: "Pending",
  COMPLETE: "Complete",
  PARTIAL: "Partial",
  UNAVAILABLE: "Unavailable",
  FAILED: "Failed",
};

/** An evidence status as a word. */
export function evidenceLabel(status: string): string {
  return EVIDENCE_LABELS[status] ?? status;
}
