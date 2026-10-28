// A latency sentence that names the delay from the real cadence, or no number at all.
//
// The figure is `worstCaseDelay` from lib/searchCadence, the formula the Search
// cadence page shows. A page that does not hold the family's cadence, bucket and
// settle margin passes none, and the sentence then says what the delay depends on.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { holds } from "@/components/auth/access";
import type { Cadence, CallerInfo } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { ENGLISH_CADENCE } from "@/lib/cadenceText";
import { worstCaseDelay } from "@/lib/searchCadence";

export const READ_SEARCH_CADENCE = "read:search_cadence";

/**
 * What `worstCaseDelay` needs for one family. All three or none: a part is never guessed. A null
 * bucket is a family with no buckets: the figure then bounds when evidence arrives.
 */
export interface LatencyCadence {
  cadenceSeconds: number;
  bucketSeconds: number | null;
  settleSeconds: number;
}

/** The API's cadence as the sentence needs it; null when the page was sent none. */
export function latencyCadence(cadence: Cadence | null | undefined): LatencyCadence | null {
  if (!cadence) return null;
  return {
    cadenceSeconds: cadence.cadence_seconds,
    bucketSeconds: cadence.bucket_seconds,
    settleSeconds: cadence.settle_seconds,
  };
}

/** `up to 22 min` (`en fazla 22 dk`), or null when the cadence is not known on the page. */
export function delayText(
  cadence: LatencyCadence | null | undefined,
  i18n: BehaviorI18n = ENGLISH_CADENCE,
): string | null {
  if (!cadence) return null;
  const { cadenceSeconds, bucketSeconds, settleSeconds } = cadence;
  if (![cadenceSeconds, settleSeconds].every(Number.isFinite)) return null;
  if (bucketSeconds !== null && !Number.isFinite(bucketSeconds)) return null;
  return worstCaseDelay(cadenceSeconds, bucketSeconds, settleSeconds, i18n).text;
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
  i18n: BehaviorI18n = ENGLISH_CADENCE,
): LatencyParts {
  const delay = delayText(cadence, i18n);
  return {
    lead: delay
      ? i18n.t("cadence.latency.knownLead", { subject, delay })
      : i18n.t("cadence.latency.unknownLead", { subject }),
    tail: i18n.t("cadence.latency.tail", { closing }),
  };
}

export function partsText(parts: LatencyParts, i18n: BehaviorI18n = ENGLISH_CADENCE): string {
  return `${parts.lead}${i18n.t("cadence.latency.link")}${parts.tail}`;
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
  const t = useTranslations("cadence.latency");
  return (
    <p className={className}>
      {parts.lead}
      {canLink ? <Link href="/admin/search-cadence">{t("link")}</Link> : t("link")}
      {parts.tail}
    </p>
  );
}
