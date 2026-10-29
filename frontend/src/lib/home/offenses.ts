// KPI card 3 (UR.4.2): offenses, from GET /offenses/analytics.
//
// No snapshot: the analytics counts are null. A null `active` is unknown on its
// own, so the card says "not collected" without waiting for the probe. The
// one-row probe stays for a zero the client cannot confirm: `GET /offenses?limit=1`
// has `total` 0 only when no snapshot exists. A probe that has not arrived, or
// that failed, must not turn that zero into "no open offenses".
//
// `magnitude_distribution` keys a NULL magnitude as the string "unassigned"
// (a label shared with the assignment distribution), so a key that is not a
// number is counted as "magnitude unknown", not as low.

import type { OffenseAnalytics, OffenseSummary, Page } from "@/lib/api";

import { notCollected, type Qualifier, type ReasonCode } from "./qualifiers";

export interface OffensesCard {
  open: number | null;
  critical: number | null;
  unassigned: number | null;
  exceedingSla: number | null;
  /** Open offenses with no magnitude (counted in neither critical nor below); 0 or null: no qualifier. */
  criticalUnknown: number | null;
  /** Open offenses with no age (not in the over-SLA count); 0 or null: no qualifier. */
  ageUnknown: number | null;
  /** The product's own threshold (settings), e.g. 7. */
  criticalMagnitude: number;
  /** open = critical + below + unknown; null parts are not derivable. */
  breakdown: { critical: number; below: number | null; unknown: number } | null;
  qualifiers: Qualifier[];
}

/** True when an analytics count was not collected. */
export function countIsUnknown(value: number | null): value is null {
  return value == null;
}

export function offensesCard(
  analytics: OffenseAnalytics,
  probe: Page<OffenseSummary> | null,
): OffensesCard {
  const a = analytics.aggregates;
  const active = a.active;
  const base = { criticalMagnitude: a.critical_magnitude };

  // No snapshot: every figure is unknown. A null count says that directly.
  // The probe still covers a numeric zero the client cannot confirm.
  if (countIsUnknown(active)) {
    return unknownOffenses(base, "No offense snapshot has been collected yet.", "NO_SNAPSHOT");
  }
  if (probe !== null && probe.total === 0) {
    return unknownOffenses(base, "No offense snapshot has been collected yet.", "NO_SNAPSHOT");
  }
  if (probe === null && active === 0) {
    return unknownOffenses(base, "Could not confirm that an offense snapshot exists.", "SNAPSHOT_UNCONFIRMED");
  }

  const critical = a.critical;
  // The count the backend measures wins; the distribution's non-numeric key is the fallback for
  // a backend that does not send it.
  const unknown =
    typeof a.critical_unknown === "number"
      ? a.critical_unknown
      : analytics.magnitude_distribution
          .filter((e) => !Number.isFinite(Number(e.key)) || e.key.trim() === "")
          .reduce((s, e) => s + e.count, 0);
  const below = critical == null ? null : active - critical - unknown;
  return {
    ...base,
    open: active,
    critical,
    unassigned: a.unassigned,
    exceedingSla: a.exceeding_sla,
    criticalUnknown: a.critical_unknown ?? null,
    ageUnknown: a.age_unknown ?? null,
    breakdown:
      critical == null
        ? null
        : { critical, below: below !== null && below >= 0 ? below : null, unknown },
    qualifiers: [],
  };
}

function unknownOffenses(
  base: { criticalMagnitude: number },
  reason: string,
  code: ReasonCode,
): OffensesCard {
  return {
    ...base,
    open: null,
    critical: null,
    unassigned: null,
    exceedingSla: null,
    criticalUnknown: null,
    ageUnknown: null,
    breakdown: null,
    qualifiers: [notCollected(reason, { code })],
  };
}
