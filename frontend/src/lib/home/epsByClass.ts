// EPS by source class (UR.4.3): at most five entries, so the donut stays within
// the five-slice rule. The top four classes by mean EPS, then the rest as one
// aggregate; with five classes or fewer nothing is folded.
//
// A class whose mean is null (no complete bucket) is left out and counted, never
// added as zero. UNCLASSIFIED is a class like the others; when it falls outside
// the top four it joins the aggregate and the result says so.

import type { FleetSummary, SourceClass } from "@/lib/api";
import { classLabel } from "@/lib/fleet";

import { insufficient, partial, type Qualifier } from "./qualifiers";

/** Key of the aggregate entry. Not a class: a class named OTHER also exists. */
export const REST_KEY = "__rest__";
export const REST_LABEL = "Other classes";
export const MAX_ENTRIES = 5;

export interface ClassEps {
  /** A class, or REST_KEY for the aggregate. */
  key: SourceClass | typeof REST_KEY;
  label: string;
  eps: number;
  /** Share of the total; null when the total is 0 (a share of nothing). */
  share: number | null;
}

export interface EpsByClass {
  entries: ClassEps[];
  /** Sum over every measured class, folded or not. */
  totalEps: number | null;
  /** Classes with sources but no complete bucket, left out of the entries. */
  classesWithoutData: number;
  /** What the aggregate contains, by label; empty when nothing is folded. */
  restContains: string[];
  /** True when UNCLASSIFIED is inside the aggregate. */
  unclassifiedInRest: boolean;
  restText: string | null;
  qualifiers: Qualifier[];
}

export function epsByClass(summary: FleetSummary): EpsByClass {
  const qualifiers: Qualifier[] = [];
  const measured = summary.classes
    .filter((c) => c.mean_eps != null && !Number.isNaN(c.mean_eps))
    .map((c) => ({
      key: c.source_class,
      label: classLabel(c.source_class),
      eps: c.mean_eps as number,
    }))
    .sort((a, b) => b.eps - a.eps || a.label.localeCompare(b.label));
  const classesWithoutData = summary.classes.filter(
    (c) => c.mean_eps == null && c.sources.enabled + c.sources.disabled > 0,
  ).length;

  const expected = summary.classes.reduce((s, c) => s + c.buckets.expected, 0);
  const complete = summary.classes.reduce((s, c) => s + c.buckets.complete, 0);
  if (summary.classes.some((c) => c.buckets.partial + c.buckets.missing > 0)) {
    qualifiers.push(partial(complete, expected, "intervals"));
  }

  if (measured.length === 0) {
    qualifiers.push(insufficient("No class has a complete bucket in this window.", { code: "NO_CLASS_COMPLETE" }));
    return {
      entries: [],
      totalEps: null,
      classesWithoutData,
      restContains: [],
      unclassifiedInRest: false,
      restText: null,
      qualifiers,
    };
  }

  const total = measured.reduce((s, c) => s + c.eps, 0);
  const share = (eps: number) => (total > 0 ? eps / total : null);
  const fold = measured.length > MAX_ENTRIES;
  const shown = fold ? measured.slice(0, MAX_ENTRIES - 1) : measured;
  const rest = fold ? measured.slice(MAX_ENTRIES - 1) : [];

  const entries: ClassEps[] = shown.map((c) => ({ ...c, share: share(c.eps) }));
  if (rest.length) {
    const eps = rest.reduce((s, c) => s + c.eps, 0);
    entries.push({ key: REST_KEY, label: REST_LABEL, eps, share: share(eps) });
  }
  const restContains = rest.map((c) => c.label);
  return {
    entries,
    totalEps: total,
    classesWithoutData,
    restContains,
    unclassifiedInRest: rest.some((c) => c.key === "UNCLASSIFIED"),
    restText: rest.length ? `${REST_LABEL}: ${restContains.join(", ")}` : null,
    qualifiers,
  };
}
