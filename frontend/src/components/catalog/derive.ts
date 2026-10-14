// Pure derivations for the catalog widgets (UI-3.5a). Each returns what the widget draws, with
// an unmeasured value as null (the widget prints an em dash) and never as zero.

import type {
  CatchallSender,
  CoverageSummary,
  FleetClassSummary,
  FleetSummary,
  OutageFleetSummary,
  OutageMode,
  RuleHealthCount,
  SearchCadenceItem,
} from "@/lib/api";
import { classLabel } from "@/lib/fleet";
import { truncated, type Qualifier } from "@/lib/home/qualifiers";
import { familyInfo, formatDuration } from "@/lib/searchCadence";

/** A run later than this beyond its due time is "late"; the scheduler ticks every 60 s. */
export const LATE_TOLERANCE_SECONDS = 60;

// --- health.detection -----------------------------------------------------------------

export interface DetectionView {
  /** Techniques evaluated; 0 means coverage was never evaluated (not "0 % covered"). */
  total: number;
  covered: number;
  /** Covered share in percent, or null when nothing was evaluated. */
  coveredPercent: number | null;
  /** Statuses other than COVERED with a count, most numerous first. */
  others: { status: string; count: number }[];
}

export function detectionView(c: CoverageSummary): DetectionView {
  const covered = c.by_status.COVERED ?? 0;
  return {
    total: c.total_techniques,
    covered,
    coveredPercent: c.total_techniques > 0 ? (covered / c.total_techniques) * 100 : null,
    others: Object.entries(c.by_status)
      .filter(([status, count]) => status !== "COVERED" && count > 0)
      .map(([status, count]) => ({ status, count }))
      .sort((a, b) => b.count - a.count || a.status.localeCompare(b.status)),
  };
}

/** Rule-health counts that are not HEALTHY, most numerous first; plus the total. */
export function ruleHealthView(rows: RuleHealthCount[]) {
  const total = rows.reduce((s, r) => s + r.count, 0);
  const healthy = rows.find((r) => r.status === "HEALTHY")?.count ?? 0;
  return {
    total,
    healthy,
    others: rows
      .filter((r) => r.status !== "HEALTHY" && r.count > 0)
      .sort((a, b) => b.count - a.count || a.status.localeCompare(b.status)),
  };
}

// --- health.collection ----------------------------------------------------------------

export interface CadenceRow {
  family: string;
  name: string;
  cadenceSeconds: number;
  cadence: string;
  lastRunAt: string | null;
  /** Seconds past the due time; 0 within tolerance; null when the family has not run. */
  lateSeconds: number | null;
  late: boolean;
}

/**
 * A family's run is due `cadence + settle` after its previous one. Lateness is how far the
 * latest run's age exceeds that, so a family that never ran since the upgrade is "not
 * measured", never "on time".
 */
export function cadenceRows(items: SearchCadenceItem[], nowMs: number): CadenceRow[] {
  return items.map((item) => {
    const ranAt = item.last_run ? Date.parse(item.last_run.at) : NaN;
    let lateSeconds: number | null = null;
    if (Number.isFinite(ranAt)) {
      const age = Math.max(0, (nowMs - ranAt) / 1000);
      const over = age - item.cadence_seconds - item.settle_seconds;
      lateSeconds = over > LATE_TOLERANCE_SECONDS ? Math.round(over) : 0;
    }
    return {
      family: item.family,
      name: familyInfo(item.family).name,
      cadenceSeconds: item.cadence_seconds,
      cadence: formatDuration(item.cadence_seconds),
      lastRunAt: item.last_run ? item.last_run.at : null,
      lateSeconds,
      late: lateSeconds !== null && lateSeconds > 0,
    };
  });
}

export function lateText(row: CadenceRow): string | null {
  if (row.lateSeconds === null) return null;
  // Whole minutes: the scheduler ticks once a minute, so seconds would be false precision.
  return row.lateSeconds === 0 ? "On time" : formatDuration(Math.ceil(row.lateSeconds / 60) * 60);
}

// --- table.catchall_senders -----------------------------------------------------------

export const CATCHALL_ROWS = 5;

/** The newest unreviewed senders by first sighting, and a TRUNCATED qualifier when the read was capped. */
export function newestSenders(
  items: CatchallSender[],
  total: number,
): { rows: CatchallSender[]; qualifiers: Qualifier[] } {
  const rows = [...items]
    .sort((a, b) => Date.parse(b.first_seen_at) - Date.parse(a.first_seen_at))
    .slice(0, CATCHALL_ROWS);
  return {
    rows,
    qualifiers: total > items.length ? [truncated(items.length, total)] : [],
  };
}

// --- table.fleet_classes --------------------------------------------------------------

export const FLEET_CLASS_ROWS = 8;

export interface ClassRow {
  key: string;
  label: string;
  sources: number;
  /** Mean EPS over the window; null when no complete bucket exists. */
  meanEps: number | null;
  /** Open NO_EVENTS incidents; null when the class has no monitored source to be silent. */
  silent: number | null;
}

export function classRows(s: FleetSummary): { rows: ClassRow[]; hidden: number; incomplete: boolean } {
  const withSources = s.classes.filter((c: FleetClassSummary) => c.sources.enabled + c.sources.disabled > 0);
  const all = withSources
    .map((c) => ({
      key: c.source_class,
      label: classLabel(c.source_class),
      sources: c.sources.enabled + c.sources.disabled,
      meanEps: c.mean_eps,
      silent: c.monitored_sources > 0 ? (c.open_incidents.NO_EVENTS ?? 0) : null,
    }))
    .sort((a, b) => (b.meanEps ?? -1) - (a.meanEps ?? -1) || a.label.localeCompare(b.label));
  return {
    rows: all.slice(0, FLEET_CLASS_ROWS),
    hidden: Math.max(0, all.length - FLEET_CLASS_ROWS),
    incomplete: s.classes.some((c) => c.buckets.partial + c.buckets.missing > 0),
  };
}

// --- table.outage_policy --------------------------------------------------------------

export interface OutageRow {
  key: string;
  label: string;
  mode: OutageMode;
  overrides: number;
}

export function outageView(s: OutageFleetSummary) {
  const rows: OutageRow[] = s.classes
    .filter((c) => c.outage_mode !== s.outage_default || c.outage_overrides > 0)
    .map((c) => ({
      key: c.source_class,
      label: classLabel(c.source_class),
      mode: c.outage_mode,
      overrides: c.outage_overrides,
    }))
    .sort((a, b) => b.overrides - a.overrides || a.label.localeCompare(b.label));
  return {
    rows,
    overrides: s.classes.reduce((n, c) => n + c.outage_overrides, 0),
    recordOnlyClasses: s.classes.filter((c) => c.outage_mode === "RECORD_ONLY").length,
  };
}
