// The quiet-period target of an anomaly detail page. Kept apart from `suppression.ts` so a page that
// does not read triage words (the alert detail, the alert policy) does not import them.

import type { AnomalySummary } from "@/lib/api";
import type { SuppressTarget } from "@/lib/suppression";
import { triageFamilyOf } from "@/lib/triage";

/**
 * The anomaly's family and id, or null for a subject kind that has no detector condition. A
 * rule-match incident has none: the alert policy has no rule family, so no quiet period is offered.
 */
export function anomalyTarget(
  anomaly: Pick<AnomalySummary, "id" | "subject_kind">,
): SuppressTarget | null {
  const family = triageFamilyOf(anomaly);
  return family === null || family === "rule" ? null : { via: "anomaly", family, anomalyId: anomaly.id };
}
