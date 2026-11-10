// Whether an IPS source can be judged, and what to say when it cannot.
//
// The server decides eligibility (`validation_current`): a source is collected only when its
// profile is enabled and VALID *for the current profile version*. These helpers put that fact into
// words and order the list, mirroring `waf/wafSourceHealth.ts`; a profile that was valid for
// version 2 and has since been edited is never shown as valid.

import type { IpsAdmission, IpsDimensionName, IpsSourceBehavior } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import type { Tone } from "@/lib/health";
import { isPlainKey } from "@/i18n/ownKey";

import { ENGLISH_IPS } from "./ipsI18n";

/** The fields of a source that decide its validation wording. */
export type IpsValidationFields = Pick<
  IpsSourceBehavior,
  "profile_version" | "validation_status" | "validated_profile_version" | "validation_current"
>;

export interface IpsValidationView {
  label: string;
  tone: Tone;
  /** The qualification that makes the label safe to read on its own. */
  detail: string;
}

function labelOf(i18n: BehaviorI18n, status: string): string {
  const key = `ips.bvalidation.label.${status}`;
  return isPlainKey(status) && i18n.t.has?.(key) ? i18n.t(key) : status;
}

export function ipsValidationView(
  s: IpsValidationFields,
  i18n: BehaviorI18n = ENGLISH_IPS,
): IpsValidationView {
  const say = (key: string, values?: Record<string, string | number>) =>
    i18n.t(`ips.bvalidation.${key}`, values);
  const version = String(s.profile_version);
  if (s.validation_status === "VALID" && s.validation_current) {
    return { label: labelOf(i18n, "VALID"), tone: "ok", detail: say("forProfile", { version }) };
  }
  if (s.validation_status === "VALID") {
    // Valid once, for an older mapping. Not collected until validated again.
    return {
      label: labelOf(i18n, "STALE"),
      tone: "warn",
      detail:
        s.validated_profile_version != null
          ? say("staleKnown", { validated: String(s.validated_profile_version), version })
          : say("staleOlder", { version }),
    };
  }
  switch (s.validation_status) {
    case "INVALID":
      return { label: labelOf(i18n, "INVALID"), tone: "crit", detail: say("forProfile", { version }) };
    case "INCONCLUSIVE":
      return { label: labelOf(i18n, "INCONCLUSIVE"), tone: "warn", detail: say("forProfile", { version }) };
    case "UNVALIDATED":
      return { label: labelOf(i18n, "UNVALIDATED"), tone: "", detail: say("neverValidated", { version }) };
    default:
      return { label: s.validation_status, tone: "", detail: say("profileOnly", { version }) };
  }
}

/** Collected and judged: enabled, and VALID for the current profile version. */
export function isJudged(s: IpsSourceBehavior): boolean {
  return s.enabled && s.validation_current && s.validation_status === "VALID";
}

/** An entity dimension whose admission lost entities in the latest bucket. */
export function dimensionOverflows(a: IpsAdmission): boolean {
  return (a.rejected_over_quota ?? 0) > 0 || a.admission_truncated === true;
}

/**
 * Admission overflowed in the latest bucket: entities were refused over the quota, or the bucket's
 * admission was truncated. Entities below the admission threshold are ordinary noise and never
 * count. A source with no bucket has no overflow figure at all (null, not false).
 */
export function isOverflowing(s: IpsSourceBehavior): boolean {
  return (s.latest_bucket?.admission ?? []).some(dimensionOverflows);
}

/** True when no entity dimension carries any admission figure: nothing was measured. */
export function admissionNotMeasured(s: IpsSourceBehavior): boolean {
  const rows = s.latest_bucket?.admission ?? [];
  return rows.every(
    (a) =>
      a.admitted == null &&
      a.below_admission_threshold == null &&
      a.rejected_over_quota == null &&
      a.untracked_entities == null &&
      a.untracked_events == null &&
      a.admission_truncated == null,
  );
}

export function dimensionAtCeiling(p: { active: number; active_ceiling: number }): boolean {
  return p.active_ceiling > 0 && p.active >= p.active_ceiling;
}

export function atCeiling(s: IpsSourceBehavior): boolean {
  return s.entities.some(dimensionAtCeiling);
}

/** Sources that need an analyst first: not judged, overflowing, or at the ceiling. */
export function needsAttention(s: IpsSourceBehavior): boolean {
  return !isJudged(s) || isOverflowing(s) || atCeiling(s);
}

export function sortByAttention(items: IpsSourceBehavior[]): IpsSourceBehavior[] {
  return [...items].sort(
    (a, b) =>
      Number(needsAttention(b)) - Number(needsAttention(a)) ||
      a.log_source_name.localeCompare(b.log_source_name),
  );
}

/** The search dimensions of a latest bucket, grouped by their measurement, in the API's order. */
export function groupByMeasurement(
  dimensions: ReadonlyArray<{ dimension: IpsDimensionName; measurement: string }>,
): Array<{ measurement: string; dimensions: IpsDimensionName[] }> {
  const groups = new Map<string, IpsDimensionName[]>();
  for (const d of dimensions) groups.set(d.measurement, [...(groups.get(d.measurement) ?? []), d.dimension]);
  return [...groups].map(([measurement, dims]) => ({ measurement, dimensions: dims }));
}
