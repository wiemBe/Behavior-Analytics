import { describe, expect, it } from "vitest";

import { JDBC_FLOW_STATES, JDBC_PROTOCOL_IDS } from "@/lib/api";
import { parseJdbcQuery, buildJdbcQuery, clearJdbcFilters } from "./query";

const INSTANCE = "2f4a5b6c-7d8e-4f01-9a2b-3c4d5e6f7081";

function rawOf(query: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(query).entries());
}

describe("JDBC query address", () => {
  it("parses bounded JDBC filters", () => {
    expect(parseJdbcQuery({
      q: "  db-server  ", limit: "25", offset: "40", instance_id: INSTANCE,
      protocol: "21", enabled: "false", flow_state: "RECEIVING",
    })).toEqual({
      q: "db-server", limit: 25, offset: 40, instance_id: INSTANCE,
      protocol: 21, enabled: false, flow_state: "RECEIVING",
    });
    expect(parseJdbcQuery({})).toEqual({ limit: 100, offset: 0 });

    // Invalid and out-of-range values are dropped or clamped, never sent to the API.
    expect(parseJdbcQuery({
      q: "x".repeat(250), limit: "9999", offset: "-5", instance_id: "not-a-uuid",
      protocol: "999", enabled: "maybe", flow_state: "NOPE",
    })).toEqual({ q: "x".repeat(200), limit: 200, offset: 0 });
    expect(parseJdbcQuery({ offset: "1.5", enabled: "1" })).toEqual({ limit: 100, offset: 0 });

    // Repeated parameters take the first value, as the existing log-source list does.
    expect(parseJdbcQuery({
      q: ["first", "second"], offset: ["7", "9"], flow_state: ["NOT_MEASURED", "RECEIVING"],
    })).toEqual({ limit: 100, offset: 7, q: "first", flow_state: "NOT_MEASURED" });
    expect(parseJdbcQuery({
      limit: ["25", "9999"], instance_id: [INSTANCE, "not-a-uuid"],
      protocol: ["21", "8"], enabled: ["false", "true"],
    })).toEqual({ limit: 25, offset: 0, instance_id: INSTANCE, protocol: 21, enabled: false });

    // q: exactly 200 survives, 201 is cut to 200, whitespace-only is dropped.
    expect(parseJdbcQuery({ q: "a".repeat(200) }).q).toBe("a".repeat(200));
    expect(parseJdbcQuery({ q: "a".repeat(201) }).q).toBe("a".repeat(200));
    expect(parseJdbcQuery({ q: "   \t " }).q).toBeUndefined();

    // limit: 200 survives, 201 clamps to 200, non-numeric drops to 100, and a negative
    // integer clamps to 1 just like the backend's `max(1, min(limit, api_max_page_size))`.
    expect(parseJdbcQuery({ limit: "200" }).limit).toBe(200);
    expect(parseJdbcQuery({ limit: "201" }).limit).toBe(200);
    expect(parseJdbcQuery({ limit: "abc" }).limit).toBe(100);
    expect(parseJdbcQuery({ limit: "-5" }).limit).toBe(1);
    expect(parseJdbcQuery({ limit: "-1" }).limit).toBe(1);
    expect(parseJdbcQuery({ limit: "0" }).limit).toBe(1);

    // An uppercase UUID is valid; an unsafe integer offset is dropped, never serialised as 1e+23.
    expect(parseJdbcQuery({ instance_id: INSTANCE.toUpperCase() }).instance_id)
      .toBe(INSTANCE.toUpperCase());
    expect(parseJdbcQuery({ offset: "99999999999999999999999" }).offset).toBe(0);

    for (const protocol of JDBC_PROTOCOL_IDS) {
      expect(parseJdbcQuery({ protocol: String(protocol) }).protocol).toBe(protocol);
    }
    for (const flowState of JDBC_FLOW_STATES) {
      expect(parseJdbcQuery({ flow_state: flowState }).flow_state).toBe(flowState);
    }
    expect(parseJdbcQuery({ enabled: "TRUE" }).enabled).toBe(true);
  });

  it("preserves offset filters", () => {
    const params = parseJdbcQuery({
      q: "oracle", limit: "50", offset: "60", instance_id: INSTANCE, protocol: "8",
      enabled: "true", flow_state: "NO_ATTRIBUTED_EVENTS",
    });
    const query = buildJdbcQuery(params);
    expect(query).toBe(
      "?q=oracle&instance_id=" + INSTANCE +
        "&protocol=8&enabled=true&flow_state=NO_ATTRIBUTED_EVENTS&limit=50&offset=60",
    );
    expect(parseJdbcQuery(rawOf(query))).toEqual(params);

    // A pagination link changes only the offset; the filters stay.
    const next = parseJdbcQuery(rawOf(buildJdbcQuery({ ...params, offset: 120 })));
    expect(next).toEqual({ ...params, offset: 120 });

    // Clear filters keeps only the page size and returns to the first page.
    const cleared = clearJdbcFilters(params);
    expect(cleared).toEqual({ limit: 50 });
    expect(buildJdbcQuery(cleared)).toBe("?limit=50");
    expect(parseJdbcQuery(rawOf(buildJdbcQuery(cleared)))).toEqual({ limit: 50, offset: 0 });
  });
});
