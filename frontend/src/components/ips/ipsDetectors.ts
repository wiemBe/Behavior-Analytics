// What each IPS detector measures, in which unit, and where its figures live.
//
// The unit is part of the claim. An entity-rate detector counts events in a
// bucket; fan-out counts the distinct destination ports one source IP reached;
// concentration and block ratio are shares. The list and the investigation read
// the unit from here, never from a default: a type this table does not know has
// no unit, and its figures are shown as not measured rather than as events or EPS.
// Mirrors `app/ips/behavior.py` and `app/ips/inputs.py`.

import type { IpsAnomalyDetail, IpsDetectionDetail } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import { ENGLISH_IPS } from "./ipsI18n";
import { own } from "@/i18n/ownKey";

/** `since` is the new-port detector's cumulative count since first appearance: not a per-bucket rate. */
export type IpsUnit = "events" | "ports" | "share" | "since";

export interface IpsDetectorSpec {
  unit: IpsUnit;
  /** An entity incident charts its entity; a source incident charts the source series. */
  scope: "entity" | "source";
  /** The baseline metric of the seasonal cells, or null when there is no seasonal expectation. */
  baselineMetric: string | null;
  /** The measured quantity in words, for the axis, the tooltip and the summary. */
  quantity: string;
  /** What a share is a share of. */
  of?: string;
}

const SPECS: Record<string, IpsDetectorSpec> = {
  IPS_PORT_RATE_SPIKE: {
    unit: "events",
    scope: "entity",
    baselineMetric: "entity_events",
    quantity: "Events",
  },
  IPS_DESTINATION_RATE_SPIKE: {
    unit: "events",
    scope: "entity",
    baselineMetric: "entity_events",
    quantity: "Events",
  },
  IPS_SIGNATURE_BURST: {
    unit: "events",
    scope: "entity",
    baselineMetric: "entity_events",
    quantity: "Events",
  },
  // A port with verified absence: the premise is that there is no history to compare with.
  // It is a destination-port entity's incident, so its series is the entity's own events.
  IPS_NEW_DESTINATION_PORT: {
    unit: "since",
    scope: "entity",
    baselineMetric: null,
    quantity: "Events",
  },
  IPS_SOURCE_CONCENTRATION_SPIKE: {
    unit: "share",
    scope: "source",
    baselineMetric: "top1_source_share",
    quantity: "Largest source IP's share of events",
    of: "source-IP events",
  },
  IPS_PORT_FANOUT_SPIKE: {
    unit: "ports",
    scope: "source",
    baselineMetric: "max_distinct_ports",
    quantity: "Distinct destination ports of the busiest source IP",
  },
  IPS_BLOCK_RATIO_SHIFT: {
    unit: "share",
    scope: "source",
    baselineMetric: "block_ratio",
    quantity: "Blocked share of action events",
    of: "events carrying an action",
  },
};

export function detectorSpec(type: string): IpsDetectorSpec | null {
  return own(SPECS, type) ?? null;
}

/** The measured quantity in the interface language, for the axis, the tooltip and the summary. */
export function quantityText(type: string, i18n: BehaviorI18n = ENGLISH_IPS): string {
  const spec = own(SPECS, type);
  if (!spec) return "";
  return i18n.t.has?.(`ips.detector.quantity.${type}`) ? i18n.t(`ips.detector.quantity.${type}`) : spec.quantity;
}

/** What a share is a share of, in the interface language; null when the detector is not a share. */
export function ofText(type: string, i18n: BehaviorI18n = ENGLISH_IPS): string | null {
  const spec = own(SPECS, type);
  if (!spec?.of) return null;
  return i18n.t.has?.(`ips.detector.of.${type}`) ? i18n.t(`ips.detector.of.${type}`) : spec.of;
}

/**
 * The new-port detector reports events since first appearance and expects none. It is judged
 * by an event-count materiality floor (`threshold`), not by a robust z-score against a seasonal
 * baseline: no robust score, no seasonal band, no surprise threshold.
 */
export function expectsNone(type: string): boolean {
  return type === "IPS_NEW_DESTINATION_PORT";
}

export interface IpsFigures {
  unit: IpsUnit | null;
  observed: number | null;
  expected: number | null;
  /** Shares only: the counts the observed share was computed from. */
  numerator: number | null;
  denominator: number | null;
}

type Row = Pick<IpsAnomalyDetail, "anomaly_type" | "observed_value" | "expected_value">;

/**
 * The observed and expected figures of an incident, in the detector's own unit.
 *
 * Read from the detection block field that matches the unit; the row's own
 * `observed_value` / `expected_value` stand in only when no detection was
 * recorded. A type without a spec yields no unit and no figures: its numbers have
 * no known meaning, so none are claimed.
 */
export function ipsFigures(row: Row, d: IpsDetectionDetail | null): IpsFigures {
  const spec = detectorSpec(row.anomaly_type);
  if (!spec) return { unit: null, observed: null, expected: null, numerator: null, denominator: null };
  let observed: number | null;
  let expected: number | null;
  if (spec.unit === "ports") {
    observed = d?.observed_distinct_ports ?? (d ? null : row.observed_value);
    expected = d?.expected_distinct_ports ?? (d ? null : row.expected_value);
  } else if (spec.unit === "share") {
    observed = d?.observed_proportion ?? (d ? null : row.observed_value);
    expected = d?.expected_proportion ?? (d ? null : row.expected_value);
  } else {
    // events (per bucket) and since (cumulative) both read the events fields.
    observed = d?.observed_events ?? (d ? null : row.observed_value);
    expected = d?.expected_events ?? (d ? null : row.expected_value);
  }
  if (expectsNone(row.anomaly_type)) expected = null;
  return {
    unit: spec.unit,
    observed,
    expected,
    numerator: spec.unit === "share" ? (d?.observed_numerator ?? null) : null,
    denominator: spec.unit === "share" ? (d?.observed_denominator ?? null) : null,
  };
}
