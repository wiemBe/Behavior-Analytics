import { describe, expect, it } from "vitest";

import { ApiError, type ScheduledSearch, type SearchExecution, type SearchVersion } from "@/lib/api";
import {
  DEFAULT_SCHEDULE_CRON,
  SEARCH_LATENCY_STATEMENT,
  capMessage,
  classifySaveError,
  createBody,
  draftFromSearch,
  emptyDraft,
  parseDraft,
  previousVersion,
  queryDiffText,
  runReadout,
  runRequestFailure,
  scheduleOptions,
  scheduleSelectValue,
  searchAccessFrom,
  updateBody,
  visualizationChoices,
} from "@/components/searches/authoring";

const LATENCY =
  "This search runs in QRadar on its schedule; its result and any threshold breach appear after each run completes, not in real time. For alerting within seconds, use QRadar's own rules.";

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1",
    name: "Repeated authentication failures",
    description: "Counts failures per source",
    owner: "SOC",
    category: "Credential Access",
    mitre_techniques: ["T1110"],
    aql_query: "SELECT count(*) FROM events WHERE username IS NOT NULL",
    query_version: 2,
    schedule_cron: "17 3 * * 1",
    schedule_timezone: "UTC",
    severity: "HIGH",
    threshold_value: 10,
    threshold_operator: "GT",
    timeout_seconds: 300,
    max_time_range_hours: 24,
    max_result_rows: 500,
    visualization_type: "PIE",
    enabled: true,
    last_run_at: null,
    next_run_at: null,
    consecutive_failures: 0,
    ...over,
  };
}

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1",
    search_id: "s-1",
    query_version: 2,
    status: "COMPLETED",
    trigger: "MANUAL",
    triggered_by: "ada",
    ariel_search_id: null,
    ariel_status: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    result_count: 4,
    truncated: false,
    error_type: null,
    error_message: null,
    retry_count: 0,
    threshold_breached: false,
    ...over,
  };
}

function draft(over: Partial<ReturnType<typeof emptyDraft>> = {}) {
  return { ...emptyDraft(), name: "Nightly count", aql: "SELECT 1", ...over };
}

describe("search access", () => {
  it("treats search:write and admin:* as writers, and fails closed otherwise", () => {
    expect(
      searchAccessFrom({
        subject: "ada",
        email: null,
        permissions: ["search:write"],
        effective_permissions: [],
      }),
    ).toBe("writer");
    expect(
      searchAccessFrom({
        subject: "ada",
        email: null,
        permissions: ["admin:*"],
        effective_permissions: ["search:write"],
      }),
    ).toBe("writer");
    expect(
      searchAccessFrom({
        subject: "analyst",
        email: null,
        permissions: ["read:*", "search:execute"],
        effective_permissions: ["read:searches", "search:execute"],
      }),
    ).toBe("reader");
  });
});

describe("schedule options", () => {
  it("defaults a new search to every five minutes (D-28)", () => {
    expect(DEFAULT_SCHEDULE_CRON).toBe("*/5 * * * *");
    expect(emptyDraft().cron).toBe(DEFAULT_SCHEDULE_CRON);
    expect(scheduleOptions(DEFAULT_SCHEDULE_CRON, null)[0]).toEqual({
      value: "*/5 * * * *",
      label: "Every 5 min",
    });
  });

  it("offers the seven presets and keeps a custom cron as Custom", () => {
    const fresh = scheduleOptions("0 * * * *", null).map((option) => option.label);
    expect(fresh).toEqual([
      "Every 5 min",
      "Every 15 min",
      "Every 30 min",
      "Every 1 h",
      "Every 2 h",
      "Every 4 h",
      "Every 8 h",
    ]);
    const custom = scheduleOptions("17 3 * * 1", "17 3 * * 1");
    expect(custom[0]).toEqual({ value: "custom", label: "Custom" });
    expect(scheduleSelectValue("17 3 * * 1")).toBe("custom");
    expect(scheduleSelectValue("0 * * * *")).toBe("0 * * * *");
    expect(custom.some((option) => option.value === "17 3 * * 1")).toBe(false);
  });
});

describe("draft parsing", () => {
  it("refuses a blank name, a blank query, and a guardrail outside the caps", () => {
    expect(parseDraft(draft({ name: "  " }), "create")?.text).toBe("Name is required.");
    expect(parseDraft(draft({ name: "n".repeat(256) }), "create")?.text).toBe(
      "Name must be 255 characters or fewer.",
    );
    expect(parseDraft(draft({ aql: "   " }), "create")?.text).toBe("AQL is required.");
    expect(parseDraft(draft({ description: "d".repeat(2001) }), "edit")?.text).toBe(
      "Description must be 2,000 characters or fewer.",
    );
    expect(parseDraft(draft({ changeNote: "n".repeat(1001) }), "edit")?.text).toBe(
      "The change note must be 1,000 characters or fewer.",
    );
    expect(parseDraft(draft({ threshold: "many" }), "edit")?.text).toBe(
      "Threshold must be a number, or empty when the search has no threshold.",
    );
    expect(parseDraft(draft({ timeout: "901" }), "edit")?.text).toBe(
      "Enter a whole number of seconds, from 1 to 900.",
    );
    expect(parseDraft(draft({ windowHours: "0" }), "edit")?.text).toBe(
      "Enter a whole number of hours, from 1 to 168.",
    );
    expect(parseDraft(draft({ rows: "10001" }), "edit")?.text).toBe(
      "Enter a whole number of rows, from 1 to 10000.",
    );
    expect(parseDraft(draft({ threshold: "" }), "edit")).toBeNull();
  });

  it("creates a disabled search and updates without renaming or flipping enabled", () => {
    const created = createBody(draft({ name: "  Nightly  ", description: "  ", aql: "  SELECT 1  " }));
    expect(created.enabled).toBe(false);
    expect(created.name).toBe("Nightly");
    expect(created.description).toBeNull();
    expect(created.aql_query).toBe("  SELECT 1  ");
    expect(created.schedule_cron).toBe("*/5 * * * *");
    expect(created.threshold_value).toBeNull();
    expect(created).not.toHaveProperty("owner");

    const stored = draftFromSearch(search());
    const updated = updateBody({ ...stored, changeNote: "  tightened  " });
    expect(updated.schedule_cron).toBe("17 3 * * 1");
    expect(updated.aql_query).toBe(search().aql_query);
    expect(updated.visualization_type).toBe("PIE");
    expect(updated.change_note).toBe("tightened");
    expect(updated).not.toHaveProperty("name");
    expect(updated).not.toHaveProperty("enabled");
    expect(visualizationChoices("PIE")[0]).toBe("PIE");
    expect(visualizationChoices("TABLE")).toEqual(["TABLE", "LINE", "BAR", "SINGLE_VALUE"]);
  });
});

describe("save refusals", () => {
  it("keeps each 422 reason verbatim and puts a cron-floor string on the schedule", () => {
    const reasons = classifySaveError(
      new ApiError(422, "Unprocessable Entity", {
        message: "unsafe AQL: first; second",
        reasons: ["first reason, as sent", "second reason, as sent"],
      }),
    );
    expect(reasons).toEqual({
      kind: "reasons",
      reasons: ["first reason, as sent", "second reason, as sent"],
    });

    const floor =
      "schedule_cron '*/1 * * * *' (UTC) fires 60 seconds apart (a then b); a search must fire at least 300 seconds apart";
    expect(classifySaveError(new ApiError(422, floor, floor))).toEqual({
      kind: "schedule",
      detail: floor,
    });

    const schema = classifySaveError(
      new ApiError(422, "Unprocessable Entity", [{ type: "int_parsing", loc: ["body"], msg: "Input should be a valid integer" }]),
    );
    expect(schema).toEqual({ kind: "reasons", reasons: ["Input should be a valid integer"] });
  });

  it("keeps the cap's limit and names the permission on a 403", () => {
    const cap = classifySaveError(new ApiError(409, "the limit is 25 enabled searches", "the limit is 25 enabled searches"));
    expect(cap).toEqual({ kind: "cap", detail: "the limit is 25 enabled searches" });
    expect(capMessage("the limit is 25 enabled searches")).toBe(
      "Not enabled: the limit is 25 enabled searches. Disable another search, then enable this one.",
    );
    expect(capMessage("the limit is 25 enabled searches.")).toBe(
      "Not enabled: the limit is 25 enabled searches. Disable another search, then enable this one.",
    );
    const forbidden = classifySaveError(new ApiError(403, "no"));
    expect(forbidden.kind).toBe("forbidden");
    if (forbidden.kind === "forbidden") {
      expect(forbidden.detail).toContain("search:write");
      expect(forbidden.detail).toContain("detection_engineer");
      expect(forbidden.detail).toContain("Nothing was saved.");
    }
  });

  it("says what failed for a network error, a 404 and a 500", () => {
    const network = classifySaveError(new Error("socket"));
    const missing = classifySaveError(new ApiError(404, "missing"));
    const server = classifySaveError(new ApiError(500, "secret"));
    expect(network.kind).toBe("error");
    expect(missing.kind).toBe("error");
    expect(server.kind).toBe("error");
    if (network.kind !== "error" || missing.kind !== "error" || server.kind !== "error") return;
    expect(network.detail).toMatch(/connection/i);
    expect(missing.detail).toMatch(/no longer exists/);
    expect(server.detail).toMatch(/could not save/);
    expect(server.detail).not.toContain("secret");
  });
});

describe("query diff", () => {
  it("returns text for a changed query and nothing when the text is the same", () => {
    expect(queryDiffText("SELECT 1", "SELECT 1")).toBeNull();
    expect(queryDiffText("SELECT 1\nFROM events", "SELECT 2\nFROM events")).toBe(
      "- SELECT 1\n+ SELECT 2\n  FROM events",
    );
    const versions: SearchVersion[] = [
      { version: 1, aql_query: "old", changed_by: null, change_note: null, created_at: "2026-10-15T00:00:00Z" },
      { version: 3, aql_query: "future", changed_by: null, change_note: null, created_at: "2026-10-16T00:00:00Z" },
      { version: 2, aql_query: "previous", changed_by: null, change_note: null, created_at: "2026-10-15T12:00:00Z" },
    ];
    expect(previousVersion(versions, 3)?.aql_query).toBe("previous");
    expect(previousVersion([], 1)).toBeNull();
  });
});

describe("run readout", () => {
  it("states the search latency sentence", () => {
    expect(SEARCH_LATENCY_STATEMENT).toBe(LATENCY);
  });

  it("treats a truncated count as a lower bound and a missing count as unmeasured", () => {
    const truncated = runReadout(execution({ truncated: true, result_count: 500, threshold_breached: false }), 1);
    expect(truncated.tone).toBe("warn");
    expect(truncated.text).toContain("at least 500");
    expect(truncated.text).toContain("no verdict");
    expect(truncated.text).not.toContain("returned 500");

    const breached = runReadout(execution({ truncated: true, result_count: 500, threshold_breached: true }), null);
    expect(breached.text).toContain("already meets the threshold");

    const unknown = runReadout(execution({ truncated: true, result_count: null }), null);
    expect(unknown.text).toContain("not shown as zero");
    expect(unknown.text).not.toMatch(/returned 0/);

    const measured = runReadout(execution({ status: "COMPLETED", result_count: 0, truncated: false }), null);
    expect(measured.tone).toBe("neutral");
    expect(measured.text).toContain("returned 0 rows");

    const missing = runReadout(execution({ status: "COMPLETED", result_count: null, truncated: false }), null);
    expect(missing.text).toContain("row count was not recorded");
    expect(missing.text).not.toContain("0 rows");
  });

  it("names a failed run's error type and the alert threshold without inventing a cause", () => {
    const failed = runReadout(
      execution({ status: "FAILED", result_count: null, error_type: "TIMEOUT" }),
      2,
    );
    expect(failed.tone).toBe("crit");
    expect(failed.text).toContain("Error type TIMEOUT");
    expect(failed.text).toContain("exceeded its timeout");
    expect(failed.text).toContain("Consecutive failures recorded: 2");
    expect(failed.text).toContain("after 3");

    const unnamed = runReadout(
      execution({ status: "FAILED", result_count: null, error_type: "ARIEL_BOOM" }),
      null,
    );
    expect(unnamed.text).toContain("Error type ARIEL_BOOM.");
    expect(unnamed.text).not.toContain("The query was rejected");
    expect(unnamed.text).toContain("after 3 consecutive failed runs");
    expect(unnamed.text).not.toContain("recorded:");

    const blank = runReadout(execution({ status: "CANCELLED", result_count: null, error_type: null }), null);
    expect(blank.text).toContain("ended CANCELLED");
    expect(blank.text).toContain("No error type was recorded.");
  });

  it("names search:execute on a refused run", () => {
    expect(runRequestFailure(new ApiError(403, "no"))).toContain("search:execute");
    expect(runRequestFailure(new ApiError(404, "gone"))).toMatch(/no longer exists/);
    expect(runRequestFailure(new Error("down"))).toMatch(/did not start/);
  });
});
