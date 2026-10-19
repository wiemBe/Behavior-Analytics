// A latency sentence that names the delay from the real cadence, or no number at all.
//
// The figure is `worstCaseDelay` from lib/searchCadence, the formula the Search
// cadence page shows. A page that does not hold the family's cadence, bucket and
// settle margin passes none, and the sentence then says what the delay depends on.

import Link from "next/link";

import { holds } from "@/components/auth/access";
import type { CallerInfo } from "@/lib/api";
import { worstCaseDelay } from "@/lib/searchCadence";

export const READ_SEARCH_CADENCE = "read:search_cadence";

/** What `worstCaseDelay` needs for one family. All three or none: a part is never guessed. */
export interface LatencyCadence {
  cadenceSeconds: number;
  bucketSeconds: number;
  settleSeconds: number;
}

/** `up to 22 min`, or null when the cadence is not known on the page. */
export function delayText(cadence: LatencyCadence | null | undefined): string | null {
  if (!cadence) return null;
  const { cadenceSeconds, bucketSeconds, settleSeconds } = cadence;
  if (![cadenceSeconds, bucketSeconds, settleSeconds].every(Number.isFinite)) return null;
  return worstCaseDelay(cadenceSeconds, bucketSeconds, settleSeconds).text;
}

/** The link to Search cadence is offered only to a reader that can open the page. */
export function canReadSearchCadence(caller: CallerInfo | null): boolean {
  if (caller === null) return false;
  return holds([...caller.permissions, ...caller.effective_permissions], READ_SEARCH_CADENCE);
}

/**
 * The sentence as text around the one place the link goes:
 * `lead` + "Search cadence" + `tail`.
 */
export interface LatencyParts {
  lead: string;
  tail: string;
}

export function latencyParts(
  subject: string,
  cadence: LatencyCadence | null | undefined,
  closing: string,
): LatencyParts {
  const delay = delayText(cadence);
  const when = delay
    ? `${delay} after the events arrive (the schedule is set on `
    : "after each collection of their family (the schedule is set on ";
  return {
    lead: `${subject} are judged on collected 5-minute buckets, ${when}`,
    tail: `). ${closing}`,
  };
}

export function partsText(parts: LatencyParts): string {
  return `${parts.lead}Search cadence${parts.tail}`;
}

export function CadenceLatency({
  parts,
  canLink,
  className,
}: {
  parts: LatencyParts;
  canLink: boolean;
  className: string;
}) {
  return (
    <p className={className}>
      {parts.lead}
      {canLink ? <Link href="/admin/search-cadence">Search cadence</Link> : "Search cadence"}
      {parts.tail}
    </p>
  );
}
