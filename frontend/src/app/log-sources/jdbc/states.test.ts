import { describe, expect, it } from "vitest";

import { createLocaleTranslator } from "@/i18n/testTranslator";
import {
  JDBC_ATTEMPT_STATES, JDBC_COMPLETENESS, JDBC_CONFIG_STATES, JDBC_CONNECTION_CODES,
  JDBC_FIELD_STATE_CODES, JDBC_FIELD_STATE_REASONS, JDBC_FAILURE_CODES, JDBC_FLOW_STATES,
  JDBC_INCONSISTENCIES, JDBC_MEASUREMENTS, JDBC_ORIGINS, JDBC_REASON_CODES, JDBC_SUPPORT,
} from "@/lib/api";
import { jdbcMeasurementView, jdbcStateLabel, type JdbcStateKind } from "./states";

const say = createLocaleTranslator("en");

// The arrays are the single source; `Record<JdbcStateKind, …>` forces a new kind into this test,
// and the compile-time catalog check in `states.ts` forces a new value to have a label.
const ENUMS: Record<JdbcStateKind, readonly string[]> = {
  flow: JDBC_FLOW_STATES,
  measurement: JDBC_MEASUREMENTS,
  connection: JDBC_CONNECTION_CODES,
  fieldState: JDBC_FIELD_STATE_CODES,
  inconsistency: JDBC_INCONSISTENCIES,
  completeness: JDBC_COMPLETENESS,
  origin: JDBC_ORIGINS,
  support: JDBC_SUPPORT,
  attempt: JDBC_ATTEMPT_STATES,
  config: JDBC_CONFIG_STATES,
  reason: JDBC_REASON_CODES,
  failure: JDBC_FAILURE_CODES,
  fieldReason: JDBC_FIELD_STATE_REASONS,
};

describe("JDBC presentation helpers", () => {
  it("renders fixed safe states only", () => {
    const unavailable = jdbcStateLabel("flow", "UNKNOWN_CODE_FROM_UPSTREAM", say);
    expect(unavailable).toBe("Unavailable");

    for (const [kind, codes] of Object.entries(ENUMS) as [JdbcStateKind, readonly string[]][]) {
      const labels = codes.map((code) => jdbcStateLabel(kind, code, say));
      // Every known value has a real label; no two values of a set read the same.
      expect(labels.every((label) => label.length > 0 && label !== unavailable)).toBe(true);
      expect(new Set(labels).size).toBe(codes.length);
      // An unknown code and arbitrary upstream text both collapse to the one fixed fallback.
      expect(jdbcStateLabel(kind, "CONNECTED; password=hunter2", say)).toBe(unavailable);
      expect(jdbcStateLabel(kind, null, say)).toBe(
        kind === "connection" ? "Not reported" : unavailable,
      );
    }

    // The allowlist guard is pinned: `unreported` has a catalog key under `connection` but is
    // not a connection *status code*, so it must not leak through as "Not reported". A code that
    // belongs to another enum (`SET` is a field state, `CONNECTED` a connection code) is rejected
    // when asked of a different kind. Removing `CODES[kind].includes` makes these three fail.
    expect(jdbcStateLabel("connection", "unreported", say)).toBe(unavailable);
    expect(jdbcStateLabel("flow", "SET", say)).toBe(unavailable);
    expect(jdbcStateLabel("flow", "CONNECTED", say)).toBe(unavailable);
  });

  it("keeps nullable measurements", () => {
    expect(jdbcMeasurementView({ event_count: null, average_eps: null, collected_at: null }))
      .toEqual({ eventCount: null, averageEps: null, collectedAt: null });
    expect(jdbcMeasurementView({
      event_count: 0, average_eps: 0, collected_at: "2026-11-21T10:00:00Z",
    })).toEqual({ eventCount: 0, averageEps: 0, collectedAt: "2026-11-21T10:00:00Z" });
    expect(jdbcMeasurementView({ event_count: 42, average_eps: 0.14, collected_at: null }))
      .toEqual({ eventCount: 42, averageEps: 0.14, collectedAt: null });
  });
});
