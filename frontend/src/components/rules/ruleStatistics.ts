// Presentation rules for the rule-statistics screen.
//
// Pure functions: the labels, the numbers and the URL the screen reads. Nothing
// here fetches, and nothing turns a missing figure into a zero.

import type { RuleCountRow, RuleStatisticsWindow } from "@/lib/api";
import { formatCount } from "@/lib/format";


export const STATS_PATH = "/rules/statistics";

export const DAY_CHOICES = [1, 7, 30] as const;
export type DayChoice = (typeof DAY_CHOICES)[number];
export const DEFAULT_DAYS: DayChoice = 7;

export type Rank = "events" | "matches";
export const DEFAULT_RANK: Rank = "events";

/** The origin filter: "all", a concrete origin, or "unknown" for a null one. */
export const UNKNOWN_ORIGIN = "unknown";
export const ALL_ORIGINS = "all";

export function parseDays(raw: string | undefined): DayChoice {
  const n = Number(raw);
  return (DAY_CHOICES as readonly number[]).includes(n) ? (n as DayChoice) : DEFAULT_DAYS;
}

export function parseRank(raw: string | undefined): Rank {
  return raw === "matches" ? "matches" : DEFAULT_RANK;
}

export function parseOrigin(raw: string | undefined, present: string[]): string {
  return raw !== undefined && present.includes(raw) ? raw : ALL_ORIGINS;
}

export interface StatsQuery {
  days: DayChoice;
  rank: Rank;
  origin: string;
}

/** The screen's URL for a query. Defaults are left out so a link stays short. */
export function statsHref(q: StatsQuery): string {
  const parts: string[] = [];
  if (q.days !== DEFAULT_DAYS) parts.push(`days=${q.days}`);
  if (q.rank !== DEFAULT_RANK) parts.push(`rank=${q.rank}`);
  if (q.origin !== ALL_ORIGINS) parts.push(`origin=${encodeURIComponent(q.origin)}`);
  return parts.length ? `${STATS_PATH}?${parts.join("&")}` : STATS_PATH;
}

const TYPE_ORDER = ["EVENT", "COMMON", "FLOW", "OFFENSE", "BUILDINGBLOCK"];

const TYPE_LABELS: Record<string, string> = {
  EVENT: "Event",
  COMMON: "Common",
  FLOW: "Flow",
  OFFENSE: "Offense",
  BUILDINGBLOCK: "Type not reported",
};

export const UNKNOWN_TYPE_LABEL = "Unknown type";
export const UNKNOWN_ORIGIN_LABEL = "Unknown origin";
export const BUILDING_BLOCK_TYPE_NOTE =
  "QRadar's building-block list does not say whether the block tests events or flows.";

function titleCase(raw: string): string {
  const s = raw.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function typeLabel(type: string | null): string {
  if (type === null || type === "") return UNKNOWN_TYPE_LABEL;
  return TYPE_LABELS[type] ?? titleCase(type);
}

export function typeTitle(type: string | null): string | undefined {
  return type === "BUILDINGBLOCK" ? BUILDING_BLOCK_TYPE_NOTE : undefined;
}

export function originLabel(origin: string | null): string {
  if (origin === null || origin === "") return UNKNOWN_ORIGIN_LABEL;
  return titleCase(origin);
}

/** "Event rule", "Type not reported, building block": what a named row is. */
export function kindLabel(type: string | null, isBuildingBlock: boolean): string {
  const t = typeLabel(type);
  if (type === "BUILDINGBLOCK") return isBuildingBlock ? `${t}, building block` : t;
  if (isBuildingBlock) return `${t} building block`;
  return type === null || type === "" ? t : `${t} rule`;
}

function typeRank(type: string | null): number {
  if (type === null) return TYPE_ORDER.length + 1;
  const i = TYPE_ORDER.indexOf(type);
  return i === -1 ? TYPE_ORDER.length : i;
}

/** The origins present in the counts, in a stable order, null last. */
export function originsPresent(counts: RuleCountRow[]): string[] {
  const set = new Set<string>();
  for (const c of counts) set.add(c.origin ?? UNKNOWN_ORIGIN);
  return [...set].sort((a, b) => {
    if (a === UNKNOWN_ORIGIN) return 1;
    if (b === UNKNOWN_ORIGIN) return -1;
    return a.localeCompare(b);
  });
}

export function originFilterLabel(origin: string): string {
  if (origin === ALL_ORIGINS) return "All origins";
  return origin === UNKNOWN_ORIGIN ? UNKNOWN_ORIGIN_LABEL : originLabel(origin);
}

export interface PivotRow {
  type: string | null;
  enabled: number;
  disabled: number;
  total: number;
}

export interface PivotGroup {
  buildingBlocks: boolean;
  rows: PivotRow[];
  totals: PivotRow;
}

/** Rule type by building block by enabled, for one origin filter. */
export function pivotCounts(counts: RuleCountRow[], origin: string): PivotGroup[] {
  const kept = counts.filter(
    (c) => origin === ALL_ORIGINS || (c.origin ?? UNKNOWN_ORIGIN) === origin,
  );
  return [false, true]
    .map((bb) => {
      const byType = new Map<string | null, PivotRow>();
      const totals: PivotRow = { type: null, enabled: 0, disabled: 0, total: 0 };
      for (const c of kept.filter((k) => k.is_building_block === bb)) {
        const row = byType.get(c.rule_type) ?? {
          type: c.rule_type,
          enabled: 0,
          disabled: 0,
          total: 0,
        };
        if (c.enabled) {
          row.enabled += c.count;
          totals.enabled += c.count;
        } else {
          row.disabled += c.count;
          totals.disabled += c.count;
        }
        row.total += c.count;
        totals.total += c.count;
        byType.set(c.rule_type, row);
      }
      const rows = [...byType.values()].sort(
        (a, b) =>
          typeRank(a.type) - typeRank(b.type) || (a.type ?? "").localeCompare(b.type ?? ""),
      );
      return { buildingBlocks: bb, rows, totals };
    })
    .filter((g) => g.rows.length > 0);
}

export { formatCount } from "@/lib/format";

/** One column-wide rule: two decimals, with a threshold for a positive sub-cent rate. */
export function formatRate(perSecond: number): string {
  if (perSecond > 0 && perSecond < 0.01) return "< 0.01";
  return perSecond.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** A benchmark is a small real number; keep it readable without inventing digits. */
export function formatCapacity(value: number | null): string {
  if (value === null) return "—";
  if (value === 0) return "0";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 1) return value.toFixed(2);
  return Number(value.toPrecision(3)).toString();
}

/** "6 d 23 h", "5 h 30 min", "45 min": the two largest non-zero units. */
export function formatSpan(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const parts: string[] = [];
  if (d > 0) parts.push(`${d} d`);
  if (h > 0) parts.push(`${h} h`);
  if (parts.length < 2 && m > 0 && d === 0) parts.push(`${m} min`);
  return parts.length ? parts.join(" ") : total === 0 ? "0 min" : "under 1 min";
}

export function daysLabel(days: number): string {
  return days === 1 ? "day" : "days";
}

export type WindowState = "complete" | "partial" | "none";

/** No complete window at all is INSUFFICIENT_DATA; some is PARTIAL. */
export function windowState(w: RuleStatisticsWindow): WindowState {
  if (w.complete) return "complete";
  return w.complete_windows === 0 ? "none" : "partial";
}

/** "167 of 168 windows complete, 1 truncated": only the non-zero shortfalls. */
export function windowBreakdown(w: RuleStatisticsWindow): string {
  const parts = [
    `${formatCount(w.complete_windows)} of ${formatCount(w.expected_windows)} windows complete`,
  ];
  if (w.truncated_windows > 0) parts.push(`${formatCount(w.truncated_windows)} truncated`);
  if (w.failed_windows > 0) parts.push(`${formatCount(w.failed_windows)} failed`);
  if (w.missing_windows > 0) parts.push(`${formatCount(w.missing_windows)} missing`);
  return parts.join(", ");
}

export const LOWER_BOUND_MEANING =
  "Flow matches are not searched, so this count proves the rule fired but may undercount.";
