// What the QRadar detail section shows, decided apart from the markup so a test can read it.
//
// Three jobs. The pair: how an HA pair reads (a healthy pair's standby is expected and never
// warned). The join: the managed hosts QRadar's REST API lists against the hosts that sent
// Health Metrics, by hostname, losing neither side. The trends: the stored series as lines with a
// break wherever windows are missing, never a line across a gap.

import type {
  QRadarConsoleHistory,
  QRadarHistorySeries,
  QRadarHostEntry,
  QRadarHostMember,
  QRadarHostMetrics,
  QRadarMetricCell,
  QRadarPairState,
} from "@/lib/api";

export type Tone = "neutral" | "warn" | "crit";

// --- the pair ----------------------------------------------------------------------------------

/**
 * The tone a pair state takes. A healthy pair is neutral: its standby is where it should be. A
 * degraded, failed-over or unclassified pair needs a look (warn); a pair with no active member is
 * failed (crit). A single host has no pair state to colour.
 */
export function pairTone(state: QRadarPairState): Tone {
  switch (state) {
    case "ha_failed":
      return "crit";
    case "ha_degraded":
    case "ha_failed_over":
    case "unknown":
      return "warn";
    default:
      return "neutral";
  }
}

/** The status string a member reports, as QRadar wrote it; null when it was not read. */
export function memberStatus(member: QRadarHostMember): string | null {
  const { status } = member;
  return status.state === "OK" && status.value ? status.value : null;
}

/** True for the standby of a healthy pair: the one status that is expected not to be ACTIVE. */
export function isExpectedStandby(entry: QRadarHostEntry, member: QRadarHostMember): boolean {
  return (
    entry.pair_state === "ha_healthy" &&
    member.role === "secondary" &&
    memberStatus(member) === "STANDBY"
  );
}

/** The statuses of a pair's members, primary first, as QRadar wrote them ("FAILED / OFFLINE"). */
export function pairStatuses(entry: QRadarHostEntry, unread: string): string {
  if (entry.unclassified.length > 0) return entry.unclassified.join(" / ");
  return entry.members.map((member) => memberStatus(member) ?? unread).join(" / ");
}

// --- the join ----------------------------------------------------------------------------------

/** One server of the table, with the Health Metrics of the host that carries its name, if any. */
export interface MemberRow {
  key: string;
  hostname: string | null;
  ip: string | null;
  role: "primary" | "secondary" | null;
  member: QRadarHostMember | null;
  metrics: QRadarHostMetrics | null;
}

export interface HostRow {
  entry: QRadarHostEntry;
  members: MemberRow[];
}

export interface HealthJoin {
  rows: HostRow[];
  /** Hosts that sent Health Metrics and are not in the managed host list: listed, never dropped. */
  orphans: QRadarHostMetrics[];
}

const lower = (name: string | null | undefined) => (name ?? "").trim().toLowerCase();
/** `qradarce2.local` -> `qradarce2`. An address-like name keeps its dots. */
const shortName = (name: string) =>
  /^[\d.]+$/.test(name) || name.includes(":") ? name : name.split(".")[0];

/**
 * Match the managed hosts' servers to the Health Metrics hosts by hostname.
 *
 * Case-insensitive and exact first. A server QRadar names `qradarce2.local` and a log source named
 * `qradarce2` are the same machine, so a short name matches next, but only when it is unambiguous
 * on both sides. A single-server host may also be matched by the managed host's own hostname.
 * Nothing is matched by guesswork beyond that: a server with no match has no metrics (its cells
 * say so), and a metrics host with no server is an orphan.
 */
export function joinHealth(
  hosts: QRadarHostEntry[],
  metrics: QRadarHostMetrics[],
): HealthJoin {
  const rows: HostRow[] = hosts.map((entry) => {
    const servers: QRadarHostMember[] = entry.members;
    const members: MemberRow[] =
      servers.length > 0
        ? servers.map((member, at) => ({
            key: `${entry.host_id ?? entry.hostname ?? "host"}-${member.server_id ?? at}`,
            hostname: member.hostname ?? (servers.length === 1 ? entry.hostname : null),
            ip: member.private_ip ?? (servers.length === 1 ? entry.private_ip : null),
            role: member.role,
            member,
            metrics: null,
          }))
        : [
            {
              key: `${entry.host_id ?? entry.hostname ?? "host"}-0`,
              hostname: entry.hostname,
              ip: entry.private_ip,
              role: null,
              member: null,
              metrics: null,
            },
          ];
    return { entry, members };
  });

  const free = new Set(metrics);
  const candidates = (row: HostRow, member: MemberRow): string[] => {
    const names = [member.hostname];
    if (row.members.length === 1) names.push(row.entry.hostname);
    return names.map(lower).filter((name) => name !== "");
  };

  // Exact names first.
  for (const row of rows) {
    for (const member of row.members) {
      const names = candidates(row, member);
      const found = [...free].find((host) => names.includes(lower(host.host)));
      if (found) {
        member.metrics = found;
        free.delete(found);
      }
    }
  }
  // Then the short name, only where one server and one metrics host carry it.
  const unmatched = rows.flatMap((row) =>
    row.members.filter((member) => member.metrics === null).map((member) => ({ row, member })),
  );
  const wantedBy = new Map<string, number>();
  for (const { row, member } of unmatched) {
    for (const name of new Set(candidates(row, member).map(shortName))) {
      wantedBy.set(name, (wantedBy.get(name) ?? 0) + 1);
    }
  }
  const offeredBy = new Map<string, QRadarHostMetrics[]>();
  for (const host of free) {
    const name = shortName(lower(host.host));
    offeredBy.set(name, [...(offeredBy.get(name) ?? []), host]);
  }
  for (const { row, member } of unmatched) {
    for (const name of new Set(candidates(row, member).map(shortName))) {
      const offered = offeredBy.get(name);
      if (wantedBy.get(name) === 1 && offered?.length === 1 && free.has(offered[0])) {
        member.metrics = offered[0];
        free.delete(offered[0]);
        break;
      }
    }
  }
  return { rows, orphans: metrics.filter((host) => free.has(host)) };
}

// --- the figures -------------------------------------------------------------------------------

/** True when the cell holds a figure (it was read, and a stale one is still a figure). */
export function cellHasValue(cell: QRadarMetricCell): cell is QRadarMetricCell & { value: number } {
  return (
    (cell.state === "OK" || cell.state === "PARTIAL" || cell.state === "STALE") &&
    typeof cell.value === "number"
  );
}

/** The distinct reasons of the cells and parts that hold no figure, in order of first use. */
export function distinctReasons(
  items: ReadonlyArray<{ state: string; reason: string | null } | null | undefined>,
): string[] {
  const seen = new Set<string>();
  for (const item of items) {
    if (!item || item.state === "OK" || item.state === "STALE" || item.state === "PARTIAL") continue;
    if (item.reason) seen.add(item.reason);
  }
  return [...seen];
}

// --- the trends --------------------------------------------------------------------------------

export type SeriesPoint = [number, number | null];

export interface HostLine {
  host: string;
  /** The line, with a null wherever windows are missing so it breaks there. */
  data: SeriesPoint[];
  /** The mount behind each measured point (the fullest-mount series only). */
  mounts: Map<number, string>;
  peak: number;
  last: number;
  measured: number;
}

export interface LineGroup {
  /** Hosts drawn, at most `MAX_LINES`, the ones with the highest peak. */
  lines: HostLine[];
  /** Hosts with a series that had points but were not drawn for want of a distinct colour. */
  omitted: string[];
  /** Hosts whose series holds no figure, with the reason the API gave. */
  unread: { host: string; state: string; reason: string }[];
}

/** One categorical colour per line: the palette has five. */
export const MAX_LINES = 5;

/** Slices a folded series spans, see `MAX_SERIES_POINTS` in the backend. */
const FOLDED_POINTS = 400;

/**
 * The longest spacing between two points that still means "the next window followed". A series
 * the backend folded keeps one point per slice of the range, so its spacing is the slice, not the
 * cadence.
 */
export function gapLimitSeconds(
  expectedIntervalSeconds: number,
  rangeSeconds: number,
  folded: boolean,
): number {
  if (folded) return Math.max(expectedIntervalSeconds * 1.5, (rangeSeconds / FOLDED_POINTS) * 2.5);
  return expectedIntervalSeconds * 1.5;
}

/** Points as a line that breaks (a null) where two neighbours are further apart than the limit. */
export function lineWithGaps(
  points: ReadonlyArray<{ t: number; value: number | null }>,
  limitSeconds: number,
): SeriesPoint[] {
  const out: SeriesPoint[] = [];
  let previous: number | null = null;
  for (const point of points) {
    if (previous !== null && (point.t - previous) / 1000 > limitSeconds) {
      out.push([(previous + point.t) / 2, null]);
    }
    out.push([point.t, point.value]);
    previous = point.t;
  }
  return out;
}

export function isFolded(series: ReadonlyArray<QRadarHistorySeries>): boolean {
  return series.some((s) => s.points.some((p) => p.windows > 1));
}

/**
 * The per-host lines of one metric. `scale` turns the stored figure into the drawn unit (the disk
 * series is stored 0 to 1 and drawn as a percentage).
 */
export function hostLines(
  console_: QRadarConsoleHistory,
  pick: (host: QRadarConsoleHistory["hosts"][number]) => QRadarHistorySeries,
  scale: number,
  limitSeconds: number,
): LineGroup {
  const lines: HostLine[] = [];
  const unread: LineGroup["unread"] = [];
  for (const host of console_.hosts) {
    const series = pick(host);
    if (series.points.length === 0) {
      unread.push({
        host: host.host,
        state: series.state,
        reason: series.reason ?? "",
      });
      continue;
    }
    const measured = series.points.map((p) => ({ t: Date.parse(p.at), value: p.value * scale }));
    const mounts = new Map<number, string>();
    for (const p of series.points) if (p.mount) mounts.set(Date.parse(p.at), p.mount);
    lines.push({
      host: host.host,
      data: lineWithGaps(measured, limitSeconds),
      mounts,
      peak: Math.max(...measured.map((p) => p.value)),
      last: measured[measured.length - 1].value,
      measured: measured.length,
    });
  }
  lines.sort((a, b) => b.peak - a.peak || a.host.localeCompare(b.host));
  const drawn = lines.slice(0, MAX_LINES);
  return {
    lines: drawn,
    omitted: lines.slice(MAX_LINES).map((line) => line.host),
    unread,
  };
}

export interface Segment {
  start: number;
  end: number;
  state: "up" | "down" | "unknown";
}

/**
 * Reachability as spans of time. A sample covers the time up to the next sample when that follows
 * within the gap limit, and one interval otherwise; between a sample and a later one that is not
 * adjacent nothing is drawn, because nothing was measured there.
 */
export function reachabilitySegments(
  points: ReadonlyArray<{ at: string; reachable: boolean | null }>,
  expectedIntervalSeconds: number,
  limitSeconds: number,
): Segment[] {
  const out: Segment[] = [];
  points.forEach((point, at) => {
    const start = Date.parse(point.at);
    const next = points[at + 1] ? Date.parse(points[at + 1].at) : null;
    const end =
      next !== null && (next - start) / 1000 <= limitSeconds
        ? next
        : start + expectedIntervalSeconds * 1000;
    out.push({
      start,
      end,
      state: point.reachable === true ? "up" : point.reachable === false ? "down" : "unknown",
    });
  });
  return out;
}

/** The latency line: the samples that have a latency, broken where windows are missing. */
export function latencyLine(
  points: ReadonlyArray<{ at: string; api_latency_ms: number | null }>,
  limitSeconds: number,
): SeriesPoint[] {
  return lineWithGaps(
    points.map((p) => ({ t: Date.parse(p.at), value: p.api_latency_ms })),
    limitSeconds,
  );
}
