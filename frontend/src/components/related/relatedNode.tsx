// The Related activity section of an anomaly page, started on the server (SOC-3b).
//
// The read is started here and handed to the section as a promise, so the page streams: the
// investigation renders at once and this section shows its loading state until the read lands. The
// promise never rejects. A failed read is a value (`RelatedRead`) the section words, so the
// anomaly page above and below it is never taken down by it.

import { getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, rateLimitedText, type AnomalySummary, type RelatedPad } from "@/lib/api";
import { triageFamilyOf } from "@/lib/triage";

import { RelatedActivitySection, type RelatedRead } from "./RelatedActivity";

async function readRelated(
  family: NonNullable<ReturnType<typeof triageFamilyOf>>,
  id: string,
  pad: RelatedPad,
  say: ReturnType<typeof asTextTranslator>,
): Promise<RelatedRead> {
  try {
    return { kind: "ok", data: await api.relatedActivity(family, id, pad) };
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.status === 403) return { kind: "failed", why: "forbidden" };
      if (err.status === 404) return { kind: "failed", why: "notFound" };
      if (err.status === 429) return { kind: "failed", why: "limited", message: rateLimitedText(err, say) };
    }
    return { kind: "failed", why: "error" };
  }
}

/**
 * The section for `anomaly`, or `null` for a kind that has no related activity (a custom-rule
 * breach has no family the endpoint knows).
 */
export async function relatedNode(anomaly: AnomalySummary, pad: RelatedPad): Promise<ReactNode> {
  const family = triageFamilyOf(anomaly);
  if (family === null) return null;
  const say = asTextTranslator(await getTranslations());
  return (
    <RelatedActivitySection
      anomalyId={anomaly.id}
      pad={pad}
      read={readRelated(family, anomaly.id, pad, say)}
      nowMs={Date.now()}
    />
  );
}
