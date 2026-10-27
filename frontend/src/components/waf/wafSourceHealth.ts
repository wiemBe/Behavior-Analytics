// Whether a WAF source can be judged, and what to say when it cannot.
//
// The server decides eligibility (`validation_current`): a source is collected
// only when its profile is enabled and VALID *for the current profile version*.
// These helpers only put that fact into words, so a profile that was valid for
// version 2 and has since been edited is never shown as valid.

import type { Tone } from "@/lib/health";
import type { WafSourceBehavior } from "@/lib/api";

import { ENGLISH_WAF, type BehaviorI18n } from "./wafI18n";

export interface ValidationView {
  label: string;
  tone: Tone;
  /** The qualification that makes the label safe to read on its own. */
  detail: string;
}

/** The validation status as a pill shows it: the code in English, the capitals of the word in Turkish. */
function labelOf(i18n: BehaviorI18n, status: string): string {
  const key = `waf.validation.label.${status}`;
  return i18n.t.has?.(key) ? i18n.t(key) : status;
}

export function validationView(
  s: WafSourceBehavior,
  i18n: BehaviorI18n = ENGLISH_WAF,
): ValidationView {
  const say = (key: string, values?: Record<string, string | number>) =>
    i18n.t(`waf.validation.${key}`, values);
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
      return {
        label: labelOf(i18n, "INCONCLUSIVE"),
        tone: "warn",
        detail: say("forProfile", { version }),
      };
    case "UNVALIDATED":
      return { label: labelOf(i18n, "UNVALIDATED"), tone: "", detail: say("neverValidated", { version }) };
    default:
      return { label: s.validation_status, tone: "", detail: say("profileOnly", { version }) };
  }
}

/** Collected and judged: enabled, and VALID for the current profile version. */
export function isJudged(s: WafSourceBehavior): boolean {
  return s.enabled && s.validation_current && s.validation_status === "VALID";
}

export function atCeiling(s: WafSourceBehavior): boolean {
  return s.active_endpoint_ceiling > 0 && s.active_endpoints >= s.active_endpoint_ceiling;
}

/**
 * Admission is overflowing when a bucket's admission was truncated or keys were
 * refused for quota. Keys below the admission threshold are ordinary noise and
 * never count.
 */
export function isOverflowing(s: WafSourceBehavior): boolean {
  return (
    (s.admission.buckets_admission_truncated ?? 0) > 0 ||
    (s.admission.rejected_over_quota ?? 0) > 0
  );
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
