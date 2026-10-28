// How IPS subjects and detectors are named on screen.
//
// An analyst reads "Destination port 445" and "Port fan-out spike", never
// `443/tcp` as a bare key or `IPS_PORT_FANOUT_SPIKE`. These live beside the IPS
// components, as the WAF names live beside the WAF ones, so the Phase A
// vocabulary stays untouched. An IPS row never takes a Phase A label: a type or
// a dimension this build does not know renders as the raw value it was sent as.
//
// The names come from the `enums.anomalyType` table (shared with the anomaly
// list) and the `ips` namespace; a helper without a `BehaviorI18n` keeps the
// English every unconverted caller and test shows today.

import type { AnomalySummary, IpsEntityRef } from "@/lib/api";
import { wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { anomalyTypeLabel } from "@/components/waf/wafLabels";

import { ENGLISH_IPS } from "./ipsI18n";

/** The IPS detector types the list filter offers, in the order shown. */
export const IPS_TYPES = [
  "IPS_PORT_RATE_SPIKE",
  "IPS_DESTINATION_RATE_SPIKE",
  "IPS_SIGNATURE_BURST",
  "IPS_NEW_DESTINATION_PORT",
  "IPS_SOURCE_CONCENTRATION_SPIKE",
  "IPS_PORT_FANOUT_SPIKE",
  "IPS_BLOCK_RATIO_SHIFT",
];

export function isIpsType(type: string | null | undefined): boolean {
  return type != null && IPS_TYPES.includes(type);
}

export function isIpsRow(a: Pick<AnomalySummary, "subject_kind">): boolean {
  return a.subject_kind === "IPS_ENTITY" || a.subject_kind === "IPS_SOURCE";
}

/** An IPS detector's name; a type this build does not know shows as sent. */
export function ipsTypeLabel(type: string, i18n: BehaviorI18n = ENGLISH_IPS): string {
  // Only the IPS types: a Phase A or WAF type on an IPS row must not borrow
  // their names, so it is shown raw even though the enum table knows it.
  return isIpsType(type) ? i18n.t(`enums.anomalyType.${type}`) : type;
}

/**
 * A detector's name for a list row of any family. An IPS row is named only from
 * the IPS table, so an unknown type is shown raw rather than borrowing the name
 * of a Phase A or WAF detector.
 */
export function rowTypeLabel(
  a: Pick<AnomalySummary, "subject_kind" | "anomaly_type">,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  return isIpsRow(a) ? ipsTypeLabel(a.anomaly_type, i18n) : anomalyTypeLabel(a.anomaly_type);
}

const IPS_DIMENSIONS = ["DESTINATION_PORT", "DESTINATION_IP", "SIGNATURE"];
const IPS_STATES = [
  "INSUFFICIENT_DATA",
  "NORMAL",
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
];

/** An entity dimension as a noun; an unknown dimension shows as sent. */
export function dimensionNoun(
  dimension: string,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  // A raw backend value is looked up only if it is one of ours: `has()` follows the prototype chain.
  return IPS_DIMENSIONS.includes(dimension) ? wordOrValue(i18n, "ips.dimension", dimension) : dimension;
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
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  if (ref.dimension === "SIGNATURE") {
    const qid = QID_KEY.exec(ref.value_key)?.[1];
    const id = qid != null ? `QID ${qid}` : ref.value_key;
    return ref.display_name
      ? `${ref.display_name} (${id})`
      : i18n.t("ips.entity.signatureId", { id });
  }
  return `${dimensionNoun(ref.dimension, i18n)} ${ref.value_key}`;
}

/**
 * What an IPS list row is about: the entity, or "Source-level". An entity row
 * whose reference is missing says so rather than borrowing the source's name.
 */
export function ipsSubjectText(
  a: Pick<AnomalySummary, "subject_kind" | "ips_entity">,
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  if (a.subject_kind === "IPS_SOURCE") return i18n.t("ips.subject.sourceLevel");
  return a.ips_entity ? entityLabel(a.ips_entity, i18n) : i18n.t("ips.subject.entityNotRecorded");
}

/**
 * What a lifecycle state means for an IPS subject. Phase A's wording is about a
 * log source's volume; this one names the subject the detector judged and claims
 * nothing about EPS.
 */
export function ipsStateMeaning(
  state: string,
  subject: "entity" | "source",
  i18n: BehaviorI18n = ENGLISH_IPS,
): string {
  if (!IPS_STATES.includes(state)) return "";
  const what = i18n.t(subject === "entity" ? "ips.stateMeaning.whatEntity" : "ips.stateMeaning.whatSource");
  return i18n.t(`ips.stateMeaning.${state}`, { what });
}

/** A lifecycle state as the timeline words it; an unknown state shows as sent. */
export function ipsStateWord(state: string, i18n: BehaviorI18n = ENGLISH_IPS): string {
  return IPS_STATES.includes(state) ? wordOrValue(i18n, "ips.stateWord", state) : state;
}
