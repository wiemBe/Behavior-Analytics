// How IPS subjects and detectors are named on screen.
//
// An analyst reads "Destination port 445" and "Port fan-out spike", never
// `443/tcp` as a bare key or `IPS_PORT_FANOUT_SPIKE`. These live beside the IPS
// components, as the WAF names live beside the WAF ones, so the Phase A
// vocabulary stays untouched. An IPS row never takes a Phase A label: a type or
// a dimension this build does not know renders as the raw value it was sent as.

import type { AnomalySummary, IpsEntityRef } from "@/lib/api";
import { anomalyTypeLabel } from "@/components/waf/wafLabels";

const IPS_TYPE_LABELS: Record<string, string> = {
  IPS_PORT_RATE_SPIKE: "Destination-port rate spike",
  IPS_DESTINATION_RATE_SPIKE: "Destination rate spike",
  IPS_SIGNATURE_BURST: "Signature burst",
  IPS_NEW_DESTINATION_PORT: "New destination port",
  IPS_SOURCE_CONCENTRATION_SPIKE: "Source concentration spike",
  IPS_PORT_FANOUT_SPIKE: "Port fan-out spike",
  IPS_BLOCK_RATIO_SHIFT: "Block-ratio shift",
};

/** The IPS detector types the list filter offers, in the order shown. */
export const IPS_TYPES = Object.keys(IPS_TYPE_LABELS);

/** The two subject kinds of IPS rows. */
export const IPS_SUBJECT_KINDS = ["IPS_ENTITY", "IPS_SOURCE"] as const;

export function isIpsType(type: string | null | undefined): boolean {
  return type != null && type in IPS_TYPE_LABELS;
}

export function isIpsRow(a: Pick<AnomalySummary, "subject_kind">): boolean {
  return a.subject_kind === "IPS_ENTITY" || a.subject_kind === "IPS_SOURCE";
}

/** An IPS detector's name; a type this build does not know shows as sent. */
export function ipsTypeLabel(type: string): string {
  return IPS_TYPE_LABELS[type] ?? type;
}

/**
 * A detector's name for a list row of any family. An IPS row is named only from
 * the IPS table, so an unknown type is shown raw rather than borrowing the name
 * of a Phase A or WAF detector.
 */
export function rowTypeLabel(a: Pick<AnomalySummary, "subject_kind" | "anomaly_type">): string {
  return isIpsRow(a) ? ipsTypeLabel(a.anomaly_type) : anomalyTypeLabel(a.anomaly_type);
}

const DIMENSION_LABELS: Record<string, string> = {
  DESTINATION_PORT: "Destination port",
  DESTINATION_IP: "Destination",
  SIGNATURE: "Signature",
};

/** An entity dimension as a noun; an unknown dimension shows as sent. */
export function dimensionNoun(dimension: string): string {
  return DIMENSION_LABELS[dimension] ?? dimension;
}

const QID_KEY = /^qid:(\d+)$/;

/**
 * The entity as an analyst names it: `Destination port 445/tcp`, `Destination
 * 10.0.4.12`, the signature's name with its QID, or `Signature QID 5000123` when
 * the source named nothing. The key is the estate's own port, address or QID
 * (design §D.4), never a source IP.
 */
export function entityLabel(
  ref: Pick<IpsEntityRef, "dimension" | "value_key" | "display_name">,
): string {
  if (ref.dimension === "SIGNATURE") {
    const qid = QID_KEY.exec(ref.value_key)?.[1];
    const id = qid != null ? `QID ${qid}` : ref.value_key;
    return ref.display_name ? `${ref.display_name} (${id})` : `Signature ${id}`;
  }
  return `${dimensionNoun(ref.dimension)} ${ref.value_key}`;
}

/** The source-level subject of an IPS_SOURCE incident. */
export const SOURCE_LEVEL = "Source-level";

/**
 * What an IPS list row is about: the entity, or "Source-level". An entity row
 * whose reference is missing says so rather than borrowing the source's name.
 */
export function ipsSubjectText(a: Pick<AnomalySummary, "subject_kind" | "ips_entity">): string {
  if (a.subject_kind === "IPS_SOURCE") return SOURCE_LEVEL;
  return a.ips_entity ? entityLabel(a.ips_entity) : "Entity not recorded";
}

/**
 * What a lifecycle state means for an IPS subject. Phase A's wording is about a
 * log source's volume; this one names the subject the detector judged and claims
 * nothing about EPS.
 */
export function ipsStateMeaning(state: string, subject: "entity" | "source"): string {
  const what = subject === "entity" ? "this entity" : "this IPS source";
  switch (state) {
    case "INSUFFICIENT_DATA":
      return `No adequate baseline for this seasonal cell yet, so ${what} is not being judged. That is not the same as being healthy.`;
    case "NORMAL":
      return "The measurement is within the expected range for this weekday and hour.";
    case "CANDIDATE":
      return "One abnormal interval recorded. Not yet an incident: promotion requires consecutive abnormal intervals.";
    case "OPEN":
      return "Confirmed incident: enough consecutive abnormal intervals to rule out a single-bucket artefact.";
    case "RECOVERING":
      return "Back inside the expected range, but held open until enough consecutive normal intervals confirm it.";
    case "RESOLVED":
      return "Closed after sustained normal behavior.";
    case "SUPPRESSED":
      return "Detected while the source was in maintenance. Recorded for history, never notified.";
    default:
      return "";
  }
}
