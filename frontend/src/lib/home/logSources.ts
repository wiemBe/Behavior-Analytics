// KPI card 2 (UR.4.2): log sources, judged and not judged.
//
// "n of m judged normal" where *judged* excludes INSUFFICIENT_DATA. It is never
// "97 % healthy": that figure would count sources nobody has judged as healthy.

import type { BehaviorSummary, SourceBehavior } from "@/lib/api";
import { healthDistribution, type HealthGroup } from "@/lib/behaviorOverview";

import { insufficient, type Qualifier } from "./qualifiers";

export const SILENT_DEFINITION =
  "Silent: the detector saw no events where some were expected (NO_EVENTS anomaly).";

export interface LogSourcesCard {
  monitored: number;
  /** All log sources QRadar knows; null when /overview was not readable. */
  total: number | null;
  /** Monitored sources that have a verdict (state other than INSUFFICIENT_DATA). */
  judged: number;
  /** Judged sources that are normal; null when none is judged. */
  normal: number | null;
  /** "38 of 41 judged normal"; null when none is judged. */
  judgedText: string | null;
  /** Sources with no verdict yet, shown as their own group, never as healthy. */
  notJudged: number;
  groups: HealthGroup[];
  silent: number;
  silentDefinition: string;
  qualifiers: Qualifier[];
}

export function logSources(
  summary: BehaviorSummary,
  sources: SourceBehavior[],
  totalLogSources: number | null,
): LogSourcesCard {
  const groups = healthDistribution(sources);
  const notJudged = sources.filter((s) => s.state === "INSUFFICIENT_DATA").length;
  const judged = sources.length - notJudged;
  const normal = groups.find((g) => g.key === "normal")?.count ?? 0;
  const qualifiers: Qualifier[] = [];
  if (judged === 0) {
    qualifiers.push(
      insufficient(
        sources.length === 0
          ? "No monitored log source."
          : "No monitored source has a verdict yet.",
      ),
    );
  }
  return {
    monitored: summary.monitored_sources,
    total: totalLogSources,
    judged,
    normal: judged === 0 ? null : normal,
    judgedText:
      judged === 0
        ? null
        : `${normal.toLocaleString("en-US")} of ${judged.toLocaleString("en-US")} judged normal`,
    notJudged,
    groups,
    silent: summary.silent_sources,
    silentDefinition: SILENT_DEFINITION,
    qualifiers,
  };
}
