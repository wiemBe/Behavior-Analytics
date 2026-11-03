// Realistic API bodies for the SIEM load tests. Every builder returns a complete, valid document;
// a test overrides only what it is about.

import type {
  ArielCallerSummary,
  ArielInFlightEntry,
  ArielLoadCurrent,
  ArielLoadSummary,
  ArielWindow,
  QRadarConsoleHealth,
  QRadarConsoleHistory,
  QRadarConsoleLatest,
  QRadarField,
  QRadarHealthHistory,
  QRadarHistorySeries,
  QRadarHostEntry,
  QRadarHostMember,
  QRadarHostMetrics,
  QRadarHosts,
  QRadarMetricCell,
  QRadarMount,
  QRadarPairState,
  QRadarRate,
} from "@/lib/api";

export const LAB_ID = "11111111-1111-4111-8111-111111111111";
export const DR_ID = "22222222-2222-4222-8222-222222222222";

export const ok = <T>(value: T): QRadarField<T> => ({
  state: "OK",
  value,
  reason_code: null,
  reason: null,
});

export const notRead = <T = never>(
  state: "NOT_COLLECTED" | "FORBIDDEN" | "FAILED",
  reason: string,
  code = "x",
): QRadarField<T> => ({ state, value: null, reason_code: code, reason });

export function window(over: Partial<ArielWindow> = {}): ArielWindow {
  return {
    submitted: 12,
    completed: 10,
    failed: 1,
    refused: 1,
    in_flight: 2,
    wall_ms_sum: 41_000,
    wall_ms_p50: 820,
    wall_ms_p95: 4_230,
    qradar_execution_ms_sum: 30_000,
    qradar_execution_ms_unknown: 0,
    qradar_execution_ms_p50: 700,
    qradar_execution_ms_p95: 3_900,
    records_returned_sum: 18_400,
    records_returned_unknown: 0,
    ...over,
  };
}

export function health(over: Partial<QRadarConsoleHealth> = {}): QRadarConsoleHealth {
  return {
    instance_id: LAB_ID,
    instance_name: "qradar-lab",
    as_of: "2026-11-02T10:14:41Z",
    cached: false,
    age_seconds: 0,
    cache_ttl_seconds: 30,
    reachable: ok(true),
    api_latency_ms: ok(182),
    version: ok("7.5.0 UP9"),
    servers: ok({
      total: 3,
      active: 3,
      unreported: 0,
      by_status: { ACTIVE: 3 },
      not_active: [],
      all_active: true,
    }),
    eps: ok({
      hosts: 3,
      average_eps: 4_120,
      peak_eps: 6_980,
      allocated_eps: 8_000,
      average_share: 0.515,
    }),
    license: ok({
      eps: { total: 10_000, allocated: 8_000, overallocated: false },
      fpm: { total: 200_000, allocated: 120_000, overallocated: false },
    }),
    ariel: {
      list_probe: ok({ held: 214, truncated: false }),
      available: ok(true),
      recent: ok({
        window_minutes: 15,
        searches: 12,
        unavailable: 0,
        unavailable_share: 0,
        last_success_at: "2026-11-02T10:14:02Z",
        last_unavailable_at: null,
      }),
      ours_in_flight: ok(2),
    },
    ...over,
  };
}

/** Every QRadar read failed: the console did not answer. */
export function unreachableHealth(over: Partial<QRadarConsoleHealth> = {}): QRadarConsoleHealth {
  const failed = <T>(code: string, reason: string) => notRead<T>("FAILED", reason, code);
  const skipped = <T>() =>
    notRead<T>("NOT_COLLECTED", "The console did not answer the reachability probe; not read.");
  return health({
    reachable: {
      state: "OK",
      value: false,
      reason_code: "timeout",
      reason: "QRadar did not answer within the read timeout.",
    },
    api_latency_ms: failed("timeout", "QRadar did not answer within the read timeout."),
    version: failed("timeout", "QRadar did not answer within the read timeout."),
    servers: skipped(),
    eps: skipped(),
    license: skipped(),
    ariel: {
      list_probe: skipped(),
      available: notRead("NOT_COLLECTED", "The platform ran no Ariel search in the window, so nothing is known."),
      recent: notRead("NOT_COLLECTED", "The platform ran no Ariel search in the window, so nothing is known."),
      ours_in_flight: ok(0),
    },
    ...over,
  });
}

export function entry(over: Partial<ArielInFlightEntry> = {}): ArielInFlightEntry {
  return {
    caller: "LOG_SOURCE_VOLUME",
    instance_id: LAB_ID,
    submitted_at: "2026-11-02T10:14:50Z",
    age_seconds: 13,
    polls: 4,
    range_seconds: 300,
    ...over,
  };
}

export function current(over: Partial<ArielLoadCurrent> = {}): ArielLoadCurrent {
  return {
    as_of: "2026-11-02T10:15:03Z",
    in_flight: {
      available: true,
      active: [
        entry(),
        entry({ caller: "WAF_COLLECTION", submitted_at: "2026-11-02T10:14:30Z", age_seconds: 33, polls: 11, range_seconds: 900 }),
      ],
      stale: [
        entry({ caller: "RULE_MATCH", submitted_at: "2026-11-02T10:08:03Z", age_seconds: 420, polls: 120, range_seconds: 86_400 }),
      ],
      stale_after_seconds: 150,
    },
    windows: {
      available: true,
      minutes_5: window({ submitted: 5, completed: 4, failed: 0, refused: 1, wall_ms_p50: 640, wall_ms_p95: 1_900 }),
      minutes_15: window(),
      minutes_60: window({ submitted: 47, completed: 44, failed: 2, refused: 1, wall_ms_p50: 910, wall_ms_p95: 12_400 }),
    },
    slots: {
      available: true,
      enforced: true,
      global_held: 4,
      global_cap: 8,
      console_pool_locks: 3,
      console_cap: 4,
      reserved_slots: 2,
    },
    qradar: { available: true, consoles: [health()] },
    ...over,
  };
}

export function caller(over: Partial<ArielCallerSummary> = {}): ArielCallerSummary {
  return {
    caller: "LOG_SOURCE_VOLUME",
    searches: 288,
    outcomes: { COMPLETED: 280, ERROR: 3, TIMEOUT: 2, REFUSED: 3 },
    wall_ms_p50: 760,
    wall_ms_p95: 3_100,
    wall_ms_max: 9_400,
    wall_ms_sum: 301_000,
    qradar_execution_ms_p50: 610,
    qradar_execution_ms_p95: 2_800,
    qradar_execution_ms_sum: 244_000,
    qradar_execution_ms_unknown: 0,
    records_returned_sum: 912_000,
    records_returned_unknown: 0,
    data_total_size_sum: null,
    data_total_size_unknown: 0,
    ...over,
  };
}

export function summary(over: Partial<ArielLoadSummary> = {}): ArielLoadSummary {
  return {
    empty: false,
    hours: 24,
    start: "2026-11-01T10:15:03+00:00",
    end: "2026-11-02T10:15:03+00:00",
    callers: [
      caller(),
      caller({
        caller: "WAF_COLLECTION",
        searches: 96,
        outcomes: { COMPLETED: 90, PROVIDER_UNAVAILABLE: 6 },
        wall_ms_p50: 2_400,
        wall_ms_p95: 14_800,
        qradar_execution_ms_p95: 13_100,
        qradar_execution_ms_sum: 188_000,
        qradar_execution_ms_unknown: 4,
        records_returned_sum: 55_000,
        records_returned_unknown: 4,
      }),
      caller({
        caller: "RULE_MATCH",
        searches: 24,
        outcomes: { COMPLETED: 24 },
        wall_ms_p50: 5_100,
        wall_ms_p95: 21_000,
        qradar_execution_ms_p95: 19_000,
        qradar_execution_ms_sum: null,
        qradar_execution_ms_unknown: 24,
        records_returned_sum: null,
        records_returned_unknown: 24,
      }),
    ],
    ...over,
  };
}

export const EMPTY_SUMMARY: ArielLoadSummary = {
  empty: true,
  hours: 24,
  start: "2026-11-01T10:15:03+00:00",
  end: "2026-11-02T10:15:03+00:00",
  message: "No Ariel search was recorded in this window.",
  callers: [],
};


// --- QRadar in detail (HLT-3) -----------------------------------------------------------------

export const REST_REASON = "QRadar returned no figure for this metric.";

export function rate(over: Partial<QRadarRate> = {}): QRadarRate {
  return { allocation: 5_000, hardware_limit: 20_000, average: 1_800, peak: 3_900, ...over };
}

export function member(over: Partial<QRadarHostMember> = {}): QRadarHostMember {
  return {
    server_id: 51,
    hostname: "qr-console",
    private_ip: "10.0.0.10",
    role: null,
    status: ok("ACTIVE"),
    ...over,
  };
}

/** A managed host that is not an HA pair. */
export function hostEntry(over: Partial<QRadarHostEntry> = {}): QRadarHostEntry {
  return {
    host_id: 53,
    hostname: "qr-console",
    private_ip: "10.0.0.10",
    public_ip: null,
    appliance_id: "3199",
    appliance_description: "Console 3199",
    version: "7.5.0",
    status: "Active",
    cpus: 16,
    total_memory_kb: 67_108_864,
    eps: ok(rate()),
    fpm: ok(rate({ allocation: 200_000, hardware_limit: 400_000, average: 90_000, peak: 150_000 })),
    role: "console",
    pair_state: "single",
    unclassified: [],
    members: [member()],
    ...over,
  };
}

/** One HA pair: the primary and the secondary with the statuses given. */
export function pairEntry(
  state: QRadarPairState,
  statuses: [string | null, string | null],
  over: Partial<QRadarHostEntry> = {},
): QRadarHostEntry {
  const read = (status: string | null): QRadarField<string> =>
    status === null ? notRead("NOT_COLLECTED", "The server list was not read.") : ok(status);
  return hostEntry({
    host_id: 60,
    hostname: "ep-ha",
    private_ip: "10.0.0.20",
    appliance_description: "Event processor 1699",
    role: "managed",
    pair_state: state,
    eps: ok(rate({ allocation: 8_000, hardware_limit: 25_000, average: 4_100, peak: 7_200 })),
    members: [
      member({ server_id: 61, hostname: "ep-ha-a", private_ip: "10.0.0.21", role: "primary", status: read(statuses[0]) }),
      member({ server_id: 62, hostname: "ep-ha-b", private_ip: "10.0.0.22", role: "secondary", status: read(statuses[1]) }),
    ],
    ...over,
  });
}

export function hostsOf(items: QRadarHostEntry[], over: Partial<QRadarHosts> = {}): QRadarField<QRadarHosts> {
  const pairs = items.filter((item) => item.pair_state !== "single").length;
  const problem = items.filter(
    (item) => item.pair_state !== "single" && item.pair_state !== "ha_healthy",
  ).length;
  return ok({ hosts: items.length, truncated: false, ha_pairs: pairs, ha_problem: problem, items, ...over });
}

export const cellOk = (value: number, over: Partial<QRadarMetricCell> = {}): QRadarMetricCell => ({
  state: "OK",
  value,
  average: value,
  samples: 30,
  reason_code: null,
  reason: null,
  ...over,
});

export const cellMissing = (
  state: "NOT_COLLECTED" | "FAILED" = "NOT_COLLECTED",
  reason = REST_REASON,
): QRadarMetricCell => ({
  state,
  value: null,
  average: null,
  samples: null,
  reason_code: "metric_not_reported",
  reason,
});

export function mount(over: Partial<QRadarMount> = {}): QRadarMount {
  return {
    mount: "/store",
    used_fraction: 0.5,
    used_percent: 50,
    used_bytes: 500e9,
    total_bytes: 1000e9,
    total_from: null,
    free_bytes: 500e9,
    level: "OK",
    ...over,
  };
}

/** The Health Metrics of one host. `diskPercent` is the fullest mount's share used. */
export function metricsHost(
  host: string,
  over: { diskPercent?: number; spill?: number; level?: QRadarMount["level"] } & Partial<QRadarHostMetrics> = {},
): QRadarHostMetrics {
  const { diskPercent = 41, spill = 0, level = "OK", ...rest } = over;
  const fullest = mount({
    used_fraction: diskPercent / 100,
    used_percent: diskPercent,
    free_bytes: (1 - diskPercent / 100) * 1000e9,
    level,
  });
  return {
    host,
    disk: {
      state: "OK",
      reason_code: null,
      reason: null,
      fullest,
      mounts: [mount({ mount: "/", used_fraction: 0.2, used_percent: 20, free_bytes: 80e9, total_bytes: 100e9 }), fullest],
    },
    system: {
      cpu_percent: cellOk(31.5),
      memory_percent: cellOk(62.25),
      load_avg_5: cellOk(2.1),
      swap_percent: cellOk(0),
      io_wait: cellOk(1.2),
      running_queries: cellOk(3),
      host_running_queries: cellOk(1),
    },
    pipeline: {
      cre_queue: cellOk(1_240),
      throttle_spill_bytes: cellOk(spill),
      throttle_spilling: spill > 0,
      ect_tcp_to_ep_disk_bytes: cellOk(0),
      flow_governor_queue_disk_bytes: cellOk(0),
      busiest_stage: { stage: "EventParser", busy_percent: 38.4 },
    },
    ...rest,
  };
}

export function consoleLatest(over: Partial<QRadarConsoleLatest> = {}): QRadarConsoleLatest {
  return {
    instance_id: LAB_ID,
    instance_name: "qradar-lab",
    window: {
      start: "2026-11-02T10:00:00Z",
      end: "2026-11-02T10:15:00Z",
      collected_at: "2026-11-02T10:16:10Z",
      cadence_seconds: 900,
    },
    metrics: { state: "OK", reason_code: null, reason: null },
    notifications: { state: "OK", reason_code: null, reason: null },
    hosts: [metricsHost("qr-console")],
    notification_list: {
      entries: [
        { host: "qr-console", qid_name: "Warning Message", events: 42, max_severity: 4, last_seen: "2026-11-02T10:09:12Z" },
        { host: "qr-console", qid_name: "SAR Sentinel: threshold crossed", events: 6, max_severity: 6, last_seen: "2026-11-02T08:40:00Z" },
      ],
      remainder: null,
      hours: 24,
      windows: 96,
      covered_from: "2026-11-01T10:15:00Z",
      covered_until: "2026-11-02T10:15:00Z",
    },
    last_metrics_window_end: "2026-11-02T10:15:00Z",
    unparseable_hosts: 0,
    dropped_mounts: 0,
    dropped_metrics: 0,
    null_values: 0,
    invalid_values: 0,
    dropped_events: 0,
    rest_sample: null,
    ...over,
  };
}

export function seriesOf(
  values: ReadonlyArray<number | null>,
  over: Partial<QRadarHistorySeries> = {},
  start = Date.parse("2026-11-01T10:00:00Z"),
  stepSeconds = 900,
): QRadarHistorySeries {
  const points = values.flatMap((value, at) =>
    value === null
      ? []
      : [{ at: new Date(start + at * stepSeconds * 1000).toISOString(), value, mount: "/store", windows: 1 }],
  );
  return { state: "OK", reason_code: null, reason: null, points, ...over };
}

export const emptySeries = (
  reason = "No window in this range holds this figure.",
): QRadarHistorySeries => ({
  state: "NOT_COLLECTED",
  reason_code: "no_window_in_range",
  reason,
  points: [],
});

export function consoleHistory(over: Partial<QRadarConsoleHistory> = {}): QRadarConsoleHistory {
  const start = Date.parse("2026-11-01T10:00:00Z");
  const sample = (at: number, reachable: boolean | null, ms: number | null) => ({
    at: new Date(start + at * 900_000).toISOString(),
    reachable,
    api_latency_ms: ms,
  });
  return {
    instance_id: LAB_ID,
    instance_name: "qradar-lab",
    metrics: { state: "OK", reason_code: null, reason: null },
    hosts: [
      {
        host: "qr-console",
        disk_fullest_fraction: seriesOf([0.4, 0.41, 0.42, 0.43]),
        cre_queue: seriesOf([900, 1_200, 1_240, 800], {}),
        throttle_spill_bytes: seriesOf([0, 0, 4_096, 0], {}),
      },
    ],
    reachability: {
      state: "OK",
      reason_code: null,
      reason: null,
      points: [sample(0, true, 180), sample(1, true, 210), sample(2, false, null), sample(3, true, 190)],
    },
    ...over,
  };
}

export function historyOf(
  consoles: QRadarConsoleHistory[] = [consoleHistory()],
  hours: 24 | 168 = 24,
): QRadarHealthHistory {
  return {
    generated_at: "2026-11-02T10:16:00Z",
    hours,
    since: hours === 24 ? "2026-11-01T10:16:00Z" : "2026-10-26T10:16:00Z",
    until: "2026-11-02T10:16:00Z",
    expected_interval_seconds: 900,
    consoles,
  };
}
