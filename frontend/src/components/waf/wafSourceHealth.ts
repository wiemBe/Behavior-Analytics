// Whether a WAF source can be judged, and what to say when it cannot.
//
// The server decides eligibility (`validation_current`): a source is collected
// only when its profile is enabled and VALID *for the current profile version*.
// These helpers only put that fact into words, so a profile that was valid for
// version 2 and has since been edited is never shown as valid.

import type { Tone } from "@/lib/health";
import type { WafSourceBehavior } from "@/lib/api";

export interface ValidationView {
  label: string;
  tone: Tone;
  /** The qualification that makes the label safe to read on its own. */
  detail: string;
}

export function validationView(s: WafSourceBehavior): ValidationView {
  if (s.validation_status === "VALID" && s.validation_current) {
    return { label: "VALID", tone: "ok", detail: `for profile v${s.profile_version}` };
  }
  if (s.validation_status === "VALID") {
    // Valid once, for an older mapping. Not collected until validated again.
    return {
      label: "STALE",
      tone: "warn",
      detail:
        s.validated_profile_version != null
          ? `VALID for v${s.validated_profile_version}; profile is now v${s.profile_version}`
          : `VALID for an older profile; profile is now v${s.profile_version}`,
    };
  }
  switch (s.validation_status) {
    case "INVALID":
      return { label: "INVALID", tone: "crit", detail: `for profile v${s.profile_version}` };
    case "INCONCLUSIVE":
      return { label: "INCONCLUSIVE", tone: "warn", detail: `for profile v${s.profile_version}` };
    case "UNVALIDATED":
      return { label: "UNVALIDATED", tone: "", detail: `profile v${s.profile_version} was never validated` };
    default:
      return { label: s.validation_status, tone: "", detail: `profile v${s.profile_version}` };
  }
}

/** Collected and judged: enabled, and VALID for the current profile version. */
export function isJudged(s: WafSourceBehavior): boolean {
  return s.enabled && s.validation_current && s.validation_status === "VALID";
}

export function atCeiling(s: WafSourceBehavior): boolean {
  return s.active_endpoint_ceiling > 0 && s.active_endpoints >= s.active_endpoint_ceiling;
}

export function isOverflowing(s: WafSourceBehavior): boolean {
  return (s.admission.buckets_admission_truncated ?? 0) > 0;
}

/** Sources that need an analyst first: not judged, overflowing, or at the ceiling. */
export function needsAttention(s: WafSourceBehavior): boolean {
  return !isJudged(s) || isOverflowing(s) || atCeiling(s);
}

export function sortByAttention(items: WafSourceBehavior[]): WafSourceBehavior[] {
  return [...items].sort(
    (a, b) =>
      Number(needsAttention(b)) - Number(needsAttention(a)) ||
      a.log_source_name.localeCompare(b.log_source_name),
  );
}
