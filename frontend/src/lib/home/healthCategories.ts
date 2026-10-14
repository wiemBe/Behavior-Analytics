// Log source health (UR.4.3): the categories that have real detection logic, each
// from the read that owns it. "Degraded" is not here: nothing detects it.
//
// Every figure is independent. A read that failed leaves its own row at null with
// the reason, and the others stand; none of them is turned into a zero.

import type { BehaviorSummary, FleetSummary, SocOverview } from "@/lib/api";

export interface HealthCategory {
  key: "silent" | "exceeding" | "new" | "conflict" | "unclassified" | "maintenance";
  label: string;
  /** Null when the read behind it was not available: shown as an em dash. */
  count: number | null;
  /** Why `count` is null; null when it is measured. */
  gap: string | null;
  /** The page that owns the list; null when none does. */
  href: string | null;
  /** What the category counts, for a `title` and the row's assistive text. */
  definition: string;
}

export interface HealthCategoryInputs {
  summary: BehaviorSummary | null;
  fleet: FleetSummary | null;
  overview: SocOverview | null;
}

export function healthCategories(inputs: HealthCategoryInputs): HealthCategory[] {
  const { summary, fleet, overview } = inputs;
  const noSummary = "Anomaly summary unavailable";
  const noFleet = "Fleet summary unavailable";
  const noOverview = "Overview unavailable";
  return [
    {
      key: "silent",
      label: "Silent",
      count: summary ? summary.silent_sources : null,
      gap: summary ? null : noSummary,
      href: "/anomalies?anomaly_type=NO_EVENTS",
      definition: "The detector saw no events where some were expected (NO_EVENTS anomaly).",
    },
    {
      key: "exceeding",
      label: "Exceeding expected volume",
      count: summary ? summary.spikes : null,
      gap: summary ? null : noSummary,
      href: "/anomalies?anomaly_type=VOLUME_SPIKE",
      definition: "Active volume spikes: observed above the expected range.",
    },
    {
      key: "new",
      label: "Newly discovered",
      count: fleet ? fleet.new_sources : null,
      gap: fleet ? null : noFleet,
      href: "/fleet/sources?review_status=NEW",
      definition: "Sources discovered and not yet reviewed.",
    },
    {
      key: "conflict",
      label: "Conflicting class",
      count: fleet ? fleet.conflict_sources : null,
      gap: fleet ? null : noFleet,
      href: "/fleet/sources?has_conflict=true",
      definition: "Sources whose classification rules disagree.",
    },
    {
      key: "unclassified",
      label: "Unclassified",
      count: fleet ? fleet.unclassified_sources : null,
      gap: fleet ? null : noFleet,
      href: "/fleet/sources?class=UNCLASSIFIED",
      definition: "Sources that no classification rule matched.",
    },
    {
      key: "maintenance",
      label: "In maintenance",
      count: overview ? overview.log_sources.in_maintenance : null,
      gap: overview ? null : noOverview,
      href: null,
      definition: "Sources in a maintenance window; their anomalies are recorded, not notified.",
    },
  ];
}
