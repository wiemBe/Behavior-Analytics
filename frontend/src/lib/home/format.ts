// Small formatters the Home tables share. A time carries its zone and its age.

import { relativeAge } from "@/lib/time";

/**
 * The Dashboard's age, omitted rather than drawn when there is nothing to say.
 * The spelling is `relativeAge`'s ("12 min ago", "under 1 min ago"). Null means
 * the instant is missing, unparsable or in the future, so a clock skew prints
 * no age beside the absolute time.
 */
export function formatAgo(iso: string | null | undefined, nowMs: number): string | null {
  const age = relativeAge(iso, nowMs);
  return age === "—" ? null : age;
}

/** A percentage with a sign and a true minus (U+2212): "+3.2 %", "−3.9 %", "0.0 %". */
export function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(value).toFixed(1)} %`;
}
