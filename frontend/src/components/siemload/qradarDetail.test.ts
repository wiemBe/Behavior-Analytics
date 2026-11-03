import { describe, expect, it } from "vitest";

import {
  cellMissing,
  cellOk,
  consoleHistory,
  emptySeries,
  hostEntry,
  member,
  metricsHost,
  pairEntry,
  seriesOf,
} from "./fixtures";
import {
  distinctReasons,
  gapLimitSeconds,
  hostLines,
  isExpectedStandby,
  joinHealth,
  latencyLine,
  lineWithGaps,
  MAX_LINES,
  pairStatuses,
  pairTone,
  reachabilitySegments,
} from "./qradarDetail";

describe("pairTone", () => {
  it("never warns about a healthy pair, and judges every other state", () => {
    expect(pairTone("ha_healthy")).toBe("neutral");
    expect(pairTone("single")).toBe("neutral");
    expect(pairTone("ha_degraded")).toBe("warn");
    expect(pairTone("ha_failed_over")).toBe("warn");
    expect(pairTone("unknown")).toBe("warn");
    expect(pairTone("ha_failed")).toBe("crit");
  });

  it("reads only the standby of a healthy pair as expected", () => {
    const healthy = pairEntry("ha_healthy", ["ACTIVE", "STANDBY"]);
    expect(isExpectedStandby(healthy, healthy.members[1])).toBe(true);
    expect(isExpectedStandby(healthy, healthy.members[0])).toBe(false);
    const degraded = pairEntry("ha_degraded", ["ACTIVE", "OFFLINE"]);
    expect(isExpectedStandby(degraded, degraded.members[1])).toBe(false);
    const failedOver = pairEntry("ha_failed_over", ["STANDBY", "ACTIVE"]);
    expect(isExpectedStandby(failedOver, failedOver.members[0])).toBe(false);
  });

  it("writes a pair's statuses primary first, as QRadar wrote them, or the unclassified ones", () => {
    expect(pairStatuses(pairEntry("ha_failed", ["FAILED", "OFFLINE"]), "none")).toBe("FAILED / OFFLINE");
    expect(pairStatuses(pairEntry("unknown", ["ACTIVE", null]), "none")).toBe("ACTIVE / none");
    expect(
      pairStatuses(pairEntry("unknown", ["ACTIVE", "Needs License"], { unclassified: ["ACTIVE", "Needs License"] }), "none"),
    ).toBe("ACTIVE / Needs License");
  });
});

describe("joinHealth", () => {
  it("matches each server of a pair to the metrics of its own hostname, case-insensitively", () => {
    const pair = pairEntry("ha_healthy", ["ACTIVE", "STANDBY"]);
    const a = metricsHost("EP-HA-A");
    const b = metricsHost("ep-ha-b");
    const { rows, orphans } = joinHealth([pair], [b, a]);
    expect(rows[0].members.map((m) => m.metrics)).toEqual([a, b]);
    expect(orphans).toEqual([]);
  });

  it("matches a short log-source name to a fully qualified server name when it is unambiguous", () => {
    const single = hostEntry({ hostname: "qradarce2", members: [member({ hostname: "qradarce2.local" })] });
    const metrics = metricsHost("qradarce2");
    const { rows, orphans } = joinHealth([single], [metrics]);
    expect(rows[0].members[0].metrics).toBe(metrics);
    expect(orphans).toEqual([]);
  });

  it("does not guess between two servers that share a short name", () => {
    const one = hostEntry({ host_id: 1, hostname: "ep.a.example", members: [member({ server_id: 1, hostname: "ep.a.example" })] });
    const two = hostEntry({ host_id: 2, hostname: "ep.b.example", members: [member({ server_id: 2, hostname: "ep.b.example" })] });
    const metrics = metricsHost("ep");
    const { rows, orphans } = joinHealth([one, two], [metrics]);
    expect(rows.flatMap((r) => r.members.map((m) => m.metrics))).toEqual([null, null]);
    expect(orphans).toEqual([metrics]);
  });

  it("gives a server without metrics no figures, and lists a metrics host without a server", () => {
    const known = hostEntry();
    const stranger = metricsHost("not-in-the-list");
    const { rows, orphans } = joinHealth([known], [stranger]);
    expect(rows[0].members[0].metrics).toBeNull();
    expect(orphans).toEqual([stranger]);
  });

  it("matches a single-server host by the managed host's own hostname when the server has none", () => {
    const entry = hostEntry({ hostname: "qr1", members: [member({ hostname: null })] });
    const metrics = metricsHost("qr1");
    expect(joinHealth([entry], [metrics]).rows[0].members[0].metrics).toBe(metrics);
  });

  it("keeps a host whose members were not returned as one row of its own", () => {
    const entry = hostEntry({ members: [] });
    const { rows } = joinHealth([entry], []);
    expect(rows[0].members).toHaveLength(1);
    expect(rows[0].members[0].member).toBeNull();
  });
});

describe("distinctReasons", () => {
  it("lists each reason once, only for parts that hold no figure", () => {
    const reasons = distinctReasons([
      cellMissing(),
      cellMissing("NOT_COLLECTED", "other"),
      cellMissing(),
      cellOk(3),
      { state: "STALE", reason: "stale" },
      null,
    ]);
    expect(reasons).toEqual(["QRadar returned no figure for this metric.", "other"]);
  });
});

describe("the trend lines", () => {
  it("breaks a line where two points are further apart than a window can explain", () => {
    const t = (n: number) => Date.parse("2026-11-01T00:00:00Z") + n * 900_000;
    const line = lineWithGaps(
      [
        { t: t(0), value: 1 },
        { t: t(1), value: 2 },
        { t: t(5), value: 3 },
      ],
      gapLimitSeconds(900, 86_400, false),
    );
    expect(line.map(([, v]) => v)).toEqual([1, 2, null, 3]);
    expect(line[2][0]).toBe((t(1) + t(5)) / 2);
  });

  it("allows a folded series the spacing of its slices", () => {
    expect(gapLimitSeconds(900, 604_800, true)).toBeGreaterThan(gapLimitSeconds(900, 604_800, false));
  });

  it("draws the disk series as a percentage, with the mount behind each point", () => {
    const group = hostLines(consoleHistory(), (h) => h.disk_fullest_fraction, 100, 1350);
    expect(group.lines).toHaveLength(1);
    expect(group.lines[0].data.map(([, v]) => v)).toEqual([40, 41, 42, 43]);
    expect(group.lines[0].peak).toBe(43);
    expect(group.lines[0].last).toBe(43);
    expect([...group.lines[0].mounts.values()][0]).toBe("/store");
  });

  it("keeps a measured zero as a zero and a host without points out of the lines", () => {
    const history = consoleHistory({
      hosts: [
        { host: "a", disk_fullest_fraction: emptySeries(), cre_queue: seriesOf([0, 0, 0]), throttle_spill_bytes: emptySeries() },
      ],
    });
    const spill = hostLines(history, (h) => h.throttle_spill_bytes, 1, 1350);
    expect(spill.lines).toEqual([]);
    expect(spill.unread[0]).toMatchObject({ host: "a", state: "NOT_COLLECTED" });
    const queue = hostLines(history, (h) => h.cre_queue, 1, 1350);
    expect(queue.lines[0].data.map(([, v]) => v)).toEqual([0, 0, 0]);
  });

  it("draws the five hosts with the highest peak and names the rest", () => {
    const hosts = Array.from({ length: 7 }, (_, at) => ({
      host: `h${at}`,
      disk_fullest_fraction: seriesOf([0.1 * (at + 1)]),
      cre_queue: seriesOf([at]),
      throttle_spill_bytes: seriesOf([0]),
    }));
    const group = hostLines(consoleHistory({ hosts }), (h) => h.disk_fullest_fraction, 100, 1350);
    expect(group.lines.map((l) => l.host)).toEqual(["h6", "h5", "h4", "h3", "h2"]);
    expect(group.lines).toHaveLength(MAX_LINES);
    expect(group.omitted).toEqual(["h1", "h0"]);
  });

  it("turns reachability samples into spans, and a sample that did not say into unknown", () => {
    const start = Date.parse("2026-11-01T10:00:00Z");
    const at = (n: number) => new Date(start + n * 900_000).toISOString();
    const spans = reachabilitySegments(
      [
        { at: at(0), reachable: true },
        { at: at(1), reachable: false },
        { at: at(2), reachable: null },
        { at: at(9), reachable: true },
      ],
      900,
      1350,
    );
    expect(spans.map((s) => s.state)).toEqual(["up", "down", "unknown", "up"]);
    expect(spans[0]).toMatchObject({ start, end: start + 900_000 });
    // The span before a gap ends after one interval, not at the far sample.
    expect(spans[2].end).toBe(Date.parse(at(2)) + 900_000);
  });

  it("leaves the latency of an unreachable sample out of the line", () => {
    const start = Date.parse("2026-11-01T10:00:00Z");
    const line = latencyLine(
      [
        { at: new Date(start).toISOString(), api_latency_ms: 200 },
        { at: new Date(start + 900_000).toISOString(), api_latency_ms: null },
        { at: new Date(start + 1_800_000).toISOString(), api_latency_ms: 210 },
      ],
      1350,
    );
    expect(line.map(([, v]) => v)).toEqual([200, null, 210]);
  });
});
