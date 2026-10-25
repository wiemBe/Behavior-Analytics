// KPI card 1 (UR.4.2): total EPS over the window, from GET /fleet/summary.
//
// The value is the sum of the classes' `mean_eps`. Each class figure is a sum
// of per-source rates over the seconds those sources actually measured
// (complete buckets only). A class with no complete bucket has a null mean and
// is left out of the sum, never added as zero. It is still counted.

import type { FleetClassSummary, FleetSummary, SourceClass } from "@/lib/api";
import { classLabel } from "@/lib/fleet";

import { insufficient, partial, type Qualifier } from "./qualifiers";

export interface TotalEps {
  /** Sum of the non-null class figures; null when no class has one. */
  meanEps: number | null;
  classesMeasured: number;
  /** Classes with sources to measure but no complete bucket. */
  classesWithoutData: number;
  /** Sum of `expected_eps` over classes with a baseline; null when none. */
  expectedEps: number | null;
  expectedCoverage: ExpectedCoverage | null;
  /**
   * The sum of `mean_eps` over baselined classes that have one: the like-for-like
   * figure to set against `expectedEps`. Null when none of them has a figure.
   */
  comparableMeanEps: number | null;
  /** (comparable - expected) / expected, in percent. Null when expected is null or 0. */
  vsExpectedPct: number | null;
  topClass: { sourceClass: SourceClass; label: string; eps: number; share: number | null } | null;
  qualifiers: Qualifier[];
}

export interface ExpectedCoverage {
  baselinedSources: number;
  monitoredSources: number;
  /** True when the expectation covers only part of the fleet. */
  partialFleet: boolean;
  text: string;
}

const hasMean = (c: FleetClassSummary) => c.mean_eps != null && !Number.isNaN(c.mean_eps);

export function totalEps(summary: FleetSummary): TotalEps {
  const classes = summary.classes;
  const measured = classes.filter(hasMean);
  const qualifiers: Qualifier[] = [];

  const meanEps = measured.length ? measured.reduce((s, c) => s + (c.mean_eps as number), 0) : null;
  const classesWithoutData = classes.filter(
    (c) => !hasMean(c) && c.sources.enabled + c.sources.disabled > 0,
  ).length;

  if (meanEps === null) {
    qualifiers.push(insufficient("No class has a complete bucket in this window.", { code: "NO_CLASS_COMPLETE" }));
  }
  const expected = classes.reduce((s, c) => s + c.buckets.expected, 0);
  const complete = classes.reduce((s, c) => s + c.buckets.complete, 0);
  if (classes.some((c) => c.buckets.partial + c.buckets.missing > 0)) {
    qualifiers.push(partial(complete, expected, "intervals"));
  }

  // Expected: only classes that have a baseline.
  const baselined = classes.filter((c) => c.baseline_sources > 0 && c.expected_eps != null);
  const expectedEps = baselined.length
    ? baselined.reduce((s, c) => s + (c.expected_eps as number), 0)
    : null;
  let expectedCoverage: ExpectedCoverage | null = null;
  if (expectedEps !== null) {
    const baselinedSources = classes.reduce((s, c) => s + c.baseline_sources, 0);
    const monitoredSources = classes.reduce((s, c) => s + c.monitored_sources, 0);
    const partialFleet = baselinedSources < monitoredSources;
    expectedCoverage = {
      baselinedSources,
      monitoredSources,
      partialFleet,
      text: `baselines cover ${baselinedSources.toLocaleString("en-US")} of ${monitoredSources.toLocaleString("en-US")} sources`,
    };
  }

  const comparable = baselined.filter(hasMean);
  const comparableMeanEps = comparable.length
    ? comparable.reduce((s, c) => s + (c.mean_eps as number), 0)
    : null;
  const vsExpectedPct =
    expectedEps !== null && expectedEps > 0 && comparableMeanEps !== null
      ? ((comparableMeanEps - expectedEps) / expectedEps) * 100
      : null;

  let topClass: TotalEps["topClass"] = null;
  if (measured.length && meanEps !== null) {
    const top = [...measured].sort(
      (a, b) =>
        (b.mean_eps as number) - (a.mean_eps as number) ||
        classLabel(a.source_class).localeCompare(classLabel(b.source_class)),
    )[0];
    topClass = {
      sourceClass: top.source_class,
      label: classLabel(top.source_class),
      eps: top.mean_eps as number,
      share: meanEps > 0 ? (top.mean_eps as number) / meanEps : null,
    };
  }

  return {
    meanEps,
    classesMeasured: measured.length,
    classesWithoutData,
    expectedEps,
    expectedCoverage,
    comparableMeanEps,
    vsExpectedPct,
    topClass,
    qualifiers,
  };
}
