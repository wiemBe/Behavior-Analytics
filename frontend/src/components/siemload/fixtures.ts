// Realistic API bodies for the SIEM load tests. Every builder returns a complete, valid document;
// a test overrides only what it is about.

import type {
  ArielCallerSummary,
  ArielInFlightEntry,
  ArielLoadCurrent,
  ArielLoadSummary,
  ArielWindow,
  QRadarConsoleHealth,
  QRadarField,
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
