import { render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { JDBC_SAFE_FIELDS, type JdbcSafeFields, type JdbcSourceDetail } from "@/lib/api";
import { JdbcDetails } from "./JdbcDetails";

const AT = "2026-11-05T18:00:00Z";
const END = "2026-11-05T19:00:00Z";
const SAFE: JdbcSafeFields = {
  database_type: "MSSQL", database_host: "db1.corp.example", tls_hostname: "tls.corp.example",
  database_port: 1433, database_name: "billing", schema_name: "dbo", table_name: "audit_events",
  select_list: "id, ts, msg", compare_field: "id", polling_interval_seconds: 60,
  start_time_local: "2026-11-01 03:00:00", prepared_statements: true, predefined_query: "Audit query",
  eps_throttle: 1000, source_label: "Billing audit", use_ssl: true, allow_untrusted: false,
  advanced_options: false,
};
const LABELS = [
  "Database type", "Database host", "TLS hostname", "Database port", "Database name", "Schema name",
  "Table name", "Select list", "Compare field", "Polling interval", "Start time", "Prepared statements",
  "Predefined query", "EPS throttle", "Source label", "Use SSL", "Allow untrusted", "Advanced options",
];
const VALUES = [
  "MSSQL", "db1.corp.example", "tls.corp.example", "1433", "billing", "dbo", "audit_events",
  "id, ts, msg", "id", "60 s", "2026-11-01 03:00:00 · Timezone unknown", "yes", "Audit query",
  "1,000 EPS", "Billing audit", "yes", "no", "no",
];

function source(): JdbcSourceDetail {
  return {
    id: "s-1", qradar_id: 7, name: "Billing DB", protocol_type_id: 8, protocol_name: "JDBC",
    qradar: {
      enabled: true, status: "CONNECTED", inconsistency: null, inventory_last_complete_at: AT,
      inventory_last_attempt_at: END, inventory_state: "COMPLETE", inventory_stale: false,
      reason_code: null,
    },
    config: {
      support: "VERIFIED", state: "COMPLETE", failure_code: null, observed_at: AT,
      last_success_at: AT, stale: false, safe_fields: { ...SAFE },
      field_states: Object.fromEntries(JDBC_SAFE_FIELDS.map((name) => [name, { code: "SET", reason: null }])),
    },
    volume: {
      measurement: "EXACT", target_start: AT, target_end: END, bucket_seconds: 3600,
      event_count: 120, average_eps: 0.033, collected_at: END, stale: false,
      reason_code: "measured", last_observation: null,
    },
    flow: { state: "RECEIVING", reason_code: "measured" }, monitoring: { enabled: true },
  };
}

function block(name: "Configuration" | "Connection" | "Flow") {
  return screen.getByRole("region", { name: name === "Configuration" ? "JDBC configuration" : name });
}
function fieldRow(label: string) {
  return screen.getByText(label, { selector: "dt" }).parentElement!;
}
function expectMissing(row: HTMLElement, reason: string) {
  const dash = within(row).getByText("—");
  expect(dash).toHaveAttribute("aria-hidden", "true");
  expect(within(row).getByTitle(reason)).toContainElement(dash);
  expect(within(row).getByText(reason)).toHaveClass("sr-only");
  expect(within(row).queryByText(/^0(?: EPS| s)?$/)).toBeNull();
}

describe("JdbcDetails", () => {
  it("all 18 safe fields: each label and value, with polling fields first", () => {
    render(<JdbcDetails data={source()} />);
    LABELS.forEach((name, i) => expect(fieldRow(name).querySelector("dd")).toHaveTextContent(VALUES[i]));
    expect(within(block("Configuration")).getAllByRole("term").slice(0, 4).map((n) => n.textContent))
      .toEqual(["Table name", "Select list", "Compare field", "Polling interval"]);
    expect(screen.getByText("Advanced configuration").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("tls.corp.example")).not.toBeVisible();
  });

  it.each(JDBC_SAFE_FIELDS)("all 18 safe fields: %s non-SET states with reasons", (name) => {
    const data = source();
    const index = JDBC_SAFE_FIELDS.indexOf(name);
    const view = render(<JdbcDetails data={data} />);
    for (const [code, reason] of [
      ["UNSET", "Unset"], ["REMOVED", "Removed"], ["UNAVAILABLE", "Field unavailable"],
    ] as const) {
      data.config.field_states[name] = { code, reason: "unsupported_value" };
      view.rerender(<JdbcDetails data={data} />);
      expectMissing(fieldRow(LABELS[index]), `${reason} · Unsupported value`);
      expect(fieldRow(LABELS[index]).querySelector("dd")).not.toHaveTextContent(VALUES[index]);
    }
    delete data.config.field_states[name];
    view.rerender(<JdbcDetails data={data} />);
    expectMissing(fieldRow(LABELS[index]), "Unavailable");
    data.config.field_states[name] = { code: "SET", reason: null };
    data.config.safe_fields![name] = null;
    view.rerender(<JdbcDetails data={data} />);
    expectMissing(fieldRow(LABELS[index]), "Unavailable");
  });

  it("units and timezone unknown", () => {
    const data = source();
    data.config.safe_fields!.eps_throttle = 0;
    render(<JdbcDetails data={data} />);
    expect(fieldRow("Polling interval")).toHaveTextContent("60 s");
    expect(fieldRow("EPS throttle")).toHaveTextContent("0 EPS");
    expect(fieldRow("Start time")).toHaveTextContent("2026-11-01 03:00:00 · Timezone unknown");
    expect(fieldRow("Start time").querySelector("time")).toBeNull();
    expect(within(block("Flow")).getByText("0.033 EPS")).toBeVisible();
    expect(within(block("Flow")).getByText("3,600 s")).toBeVisible();
    const times = document.querySelectorAll("time");
    expect(times).toHaveLength(7);
    times.forEach((time) => {
      expect(time).toHaveTextContent(/ UTC$/);
      expect([AT, END]).toContain(time.getAttribute("datetime"));
      expect(time).toHaveClass("nowrap", "num");
    });
  });

  it.each(["loading", "error", "forbidden", "not-found"] as const)("every block honesty state: load %s", (failure) => {
    render(<JdbcDetails failure={failure} />);
    const state = failure === "loading" ? screen.getByRole("status") : screen.getByText({
      error: "JDBC details could not be loaded. Reload the page to retry.",
      forbidden: "JDBC details require read:log_sources.",
      "not-found": "This source has no JDBC details in the inventory.",
    }[failure]);
    if (failure !== "loading") expect(state).not.toHaveAttribute("role");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(state).toHaveTextContent({
      loading: "Loading JDBC details…", error: "JDBC details could not be loaded. Reload the page to retry.",
      forbidden: "JDBC details require read:log_sources.", "not-found": "This source has no JDBC details in the inventory.",
    }[failure]);
    expect(screen.queryByText(/^0$/)).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
  });

  it.each([
    ["PARTIAL", "Partial"], ["NOT_COLLECTED", "Not collected"],
    ["UNVERIFIED_PROTOCOL", "Unverified protocol"], ["NOT_JDBC", "Not a JDBC source"],
  ] as const)("every block honesty state: configuration %s", (state, text) => {
    const data = source();
    data.config.state = state;
    if (state === "UNVERIFIED_PROTOCOL") data.config.support = state;
    render(<JdbcDetails data={data} />);
    expect(within(block("Configuration")).getByText(text, { selector: "p" })).toBeVisible();
    if (state === "NOT_COLLECTED" || state === "UNVERIFIED_PROTOCOL") {
      LABELS.forEach((name) => expectMissing(fieldRow(name), text));
    }
    expect(within(block("Flow")).getByText("120")).toBeVisible();
  });

  it.each([null, "timeout"] as const)("every block honesty state: stale configuration retains dated safe fields (%s)", (failure) => {
    const data = source();
    data.config.stale = true;
    data.config.failure_code = failure;
    render(<JdbcDetails data={data} />);
    expect(within(block("Configuration")).getByText("Configuration stale")).toBeVisible();
    expect(fieldRow("Database name")).toHaveTextContent("billing");
    const state = within(block("Configuration")).getByText("Configuration stale").parentElement!;
    expect(state).toHaveTextContent("Last successful read: 2026-11-05 18:00:00 UTC");
    expect(state.querySelector("time")).toBeVisible();
    if (failure) expect(within(state).getByText("Timed out")).toBeVisible();
    expect(state.closest("details")).toBeNull();
    expect(within(block("Configuration")).getByText("Configuration details").closest("details"))
      .not.toHaveAttribute("open");
  });

  it.each([
    ["CONNECTED", "Connected"], ["WARNING", "Warning"], ["CONNECTION_ERROR", "Connection error"],
    ["DISABLED_STATUS", "Disabled"], [null, "Not reported"],
  ] as const)("every block honesty state: connection %s", (status, text) => {
    const data = source();
    data.qradar.status = status;
    render(<JdbcDetails data={data} />);
    expect(within(block("Connection")).getAllByText(text)[0]).toBeVisible();
    if (status) expect(within(block("Connection")).getByText(status)).not.toBeVisible();
    if (status === "WARNING") expect(within(block("Connection")).getByText(text)).toHaveClass("warn");
    expect(within(block("Flow")).getByText("Receiving")).toBeVisible();
  });

  it.each([
    ["PARTIAL", "Flow cannot be judged for this interval.", "Partial"],
    ["TRUNCATED", "Event measurement was truncated; flow is not judged.", "Truncated"],
    ["NOT_COLLECTED", "Events were not measured for this interval.", "Not collected"],
  ] as const)("every block honesty state: flow %s", (measurement, text, missing) => {
    const data = source();
    Object.assign(data.volume, { measurement, event_count: null, average_eps: null, collected_at: null });
    data.flow = { state: "NOT_MEASURED", reason_code: measurement === "PARTIAL" ? "partial" : "not_collected" };
    render(<JdbcDetails data={data} />);
    const flow = block("Flow");
    expect(within(flow).getByText(text)).toBeVisible();
    expect(within(flow).getByText("Not measured")).toBeVisible();
    expectMissing(within(flow).getByText("Events (count)").parentElement!, missing);
    expectMissing(within(flow).getByText("Average EPS").parentElement!, missing);
    expectMissing(within(flow).getByText("Observed UTC").parentElement!, missing);
    expect(within(flow).queryByText("Receiving")).toBeNull();
  });

  it("every block honesty state: never collected inventory and failed attempt", () => {
    const data = source();
    Object.assign(data.qradar, { inventory_last_complete_at: null, inventory_state: "FAILED" });
    render(<JdbcDetails data={data} />);
    const snapshot = screen.getByText(/JDBC inventory has not been collected/);
    expect(snapshot).toHaveTextContent("Attempt: Failed");
    expect(snapshot).toHaveTextContent("2026-11-05 19:00:00 UTC");
    expect(within(block("Flow")).getByText("Receiving")).toBeVisible();
  });

  it("every block honesty state: stale inventory and partial attempt keep receiving", () => {
    const data = source();
    Object.assign(data.qradar, { inventory_stale: true, inventory_state: "PARTIAL" });
    render(<JdbcDetails data={data} />);
    expect(screen.getByText("QRadar inventory is stale.").parentElement).toHaveTextContent("Attempt: Partial");
    expect(screen.getByText("QRadar inventory is stale.").parentElement).toHaveTextContent("2026-11-05 18:00:00 UTC");
    expect(within(block("Flow")).getByText("Receiving")).toBeVisible();
  });

  it("every block honesty state: stale volume matches list wording and retains dated count", () => {
    const data = source();
    data.volume.stale = true;
    render(<JdbcDetails data={data} />);
    const flow = block("Flow");
    expect(within(flow).getByText("Volume stale")).toBeVisible();
    expect(within(flow).getByText("120")).toBeVisible();
    expect(within(flow).getAllByText("2026-11-05 19:00:00 UTC")).toHaveLength(2);
    expect(within(flow).getByText("Volume stale").parentElement).toHaveTextContent("Receiving · Volume stale");
    expect(within(flow).getByText("Volume stale").parentElement).toBeVisible();
    expect(within(flow).queryByText("Not measured")).toBeNull();
    expect(within(flow).getByText("2026-11-05 18:00:00 UTC")).toBeVisible();
  });

  it("every block honesty state: disabled keeps separately dated historical counts", () => {
    const data = source();
    data.qradar.enabled = false;
    data.flow = { state: "DISABLED_IN_QRADAR", reason_code: "disabled_in_qradar" };
    Object.assign(data.volume, { measurement: "NOT_COLLECTED", event_count: null, average_eps: null, stale: true });
    data.volume.last_observation = {
      start: AT, end: END, observed_at: END, event_count: 81, completeness: "COMPLETE", origin: "history",
    };
    render(<JdbcDetails data={data} />);
    const flow = block("Flow");
    expect(within(flow).getByText("Disabled in QRadar", { selector: "p" })).toBeVisible();
    expect(within(flow).queryByText("Volume stale")).toBeNull();
    expect(within(flow).queryByText("Receiving")).toBeNull();
    const details = within(flow).getByText("81").closest("details")!;
    expect(details).toHaveTextContent("Historical");
    expect(details).toHaveTextContent("Complete");
    expect(details).toHaveTextContent("2026-11-05 19:00:00 UTC");
    expect(within(flow).getByText("81")).not.toBeVisible();
  });

  it("every block honesty state: connected + exact zero", () => {
    const data = source();
    Object.assign(data.volume, { event_count: 0, average_eps: 0 });
    data.flow.state = "NO_ATTRIBUTED_EVENTS";
    render(<JdbcDetails data={data} />);
    expect(within(block("Connection")).getByText("Connected")).toBeVisible();
    expect(within(block("Flow")).getByText("No attributed events")).toBeVisible();
    expect(within(block("Flow")).getByText("0")).toBeVisible();
    expect(within(block("Flow")).getByText("0 EPS")).toBeVisible();
  });

  it.each([
    [0, "0 EPS"], [0.000278, "< 0.001 EPS"], [0.000999, "< 0.001 EPS"], [0.001, "0.001 EPS"],
  ])("average EPS preserves zero and small positive rates: %s", (eps, text) => {
    const data = source();
    Object.assign(data.volume, { event_count: eps === 0 ? 0 : 1, average_eps: eps });
    render(<JdbcDetails data={data} />);
    const row = fieldRow("Average EPS");
    expect(row.querySelector("dd")!.textContent).toBe(text);
    if (eps > 0) expect(within(row).queryByText("0 EPS")).toBeNull();
    expect(fieldRow("Events (count)").querySelector("dd")).toHaveTextContent(eps === 0 ? "0" : "1");
  });

  it("exact null values name their own unavailable reason", () => {
    const data = source();
    Object.assign(data.volume, {
      event_count: null, average_eps: null, collected_at: null, target_start: null, target_end: null,
    });
    render(<JdbcDetails data={data} />);
    for (const name of ["Events (count)", "Average EPS", "Window start", "Window end", "Observed UTC"]) {
      expectMissing(within(block("Flow")).getByText(name).parentElement!, "Unavailable");
    }
  });

  it.each([["MISSING", "Missing"], ["PARTIAL", "Partial"]] as const)(
    "historical null count uses its own %s completeness", (completeness, reason) => {
      const data = source();
      data.volume.last_observation = {
        start: AT, end: END, observed_at: END, event_count: null, completeness, origin: "history",
      };
      render(<JdbcDetails data={data} />);
      const details = within(block("Flow")).getByText("Flow details").closest("details")!;
      expectMissing(within(details).getByText("Events (count)").parentElement!, reason);
      expect(within(block("Flow")).getByText("120")).toBeVisible();
    },
  );

  it("configuration failure without stale flag exposes its last successful UTC read", () => {
    const data = source();
    data.config.failure_code = "network";
    render(<JdbcDetails data={data} />);
    const state = within(block("Configuration")).getByText("Network error").parentElement!;
    expect(state).toBeVisible();
    expect(state).toHaveTextContent("Last successful read: 2026-11-05 18:00:00 UTC");
    expect(state.querySelector("time")).toBeVisible();
    expect(state.closest("details")).toBeNull();
  });

  it("unknown non-null connection status uses the fixed unavailable fallback", () => {
    const data = source();
    Object.assign(data.qradar, { status: "UNKNOWN_UPSTREAM_CODE" });
    render(<JdbcDetails data={data} />);
    const connection = block("Connection");
    expect(within(connection).getAllByText("Unavailable")[0]).toBeVisible();
    expect(within(connection).getByText("QRadar status code").parentElement)
      .toHaveTextContent("Unavailable");
    expect(within(connection).queryByText("Not reported")).toBeNull();
    expect(within(connection).queryByText("UNKNOWN_UPSTREAM_CODE")).toBeNull();
  });

  it("null reason codes omit their rows and an ordinary connection uses the plain pill", () => {
    const data = source();
    const view = render(<JdbcDetails data={data} />);
    expect(within(block("Connection")).getByText("Connected")).toHaveAttribute("class", "pill");
    expect(within(block("Flow")).getByText("Flow reason")).toBeInTheDocument();
    expect(within(block("Flow")).getByText("Measurement reason")).toBeInTheDocument();
    data.flow.reason_code = null;
    data.volume.reason_code = null;
    view.rerender(<JdbcDetails data={data} />);
    expect(within(block("Flow")).queryByText("Flow reason")).toBeNull();
    expect(within(block("Flow")).queryByText("Measurement reason")).toBeNull();
    expect(within(block("Connection")).queryByText("Inventory reason")).toBeNull();
  });

  it("sections are named by their headings with unique references across instances", () => {
    render(<><JdbcDetails data={source()} /><JdbcDetails data={source()} /></>);
    const regions = screen.getAllByRole("region");
    expect(regions).toHaveLength(6);
    const ids = regions.map((region) => {
      expect(region).not.toHaveAttribute("aria-label");
      const id = region.getAttribute("aria-labelledby")!;
      const heading = document.getElementById(id);
      expect(heading).toBe(within(region).getByRole("heading", { level: 2 }));
      expect(region).toHaveAccessibleName(heading!.textContent!);
      return id;
    });
    expect(new Set(ids).size).toBe(6);
    expect(screen.getAllByRole("heading", { name: "JDBC configuration", level: 2 })).toHaveLength(2);
  });

  it("no status to flow inference", () => {
    const data = source();
    const view = render(<JdbcDetails data={data} />);
    const initialFlow = block("Flow").innerHTML;
    data.qradar.status = "CONNECTION_ERROR";
    view.rerender(<JdbcDetails data={data} />);
    expect(block("Flow").innerHTML).toBe(initialFlow);
    const initialConnection = block("Connection").innerHTML;
    data.flow.state = "NO_ATTRIBUTED_EVENTS";
    Object.assign(data.volume, { event_count: 0, average_eps: 0 });
    view.rerender(<JdbcDetails data={data} />);
    expect(block("Connection").innerHTML).toBe(initialConnection);
    expect(block("Flow")).not.toHaveTextContent(/healthy|normal|empty table/i);
  });

  it("block error isolated", () => {
    const data = source();
    const view = render(<JdbcDetails data={data} />);
    const initialFlow = block("Flow").innerHTML;
    Object.assign(data.config, { state: "PARTIAL", stale: true, failure_code: "network", safe_fields: null, field_states: {} });
    view.rerender(<JdbcDetails data={data} />);
    expect(block("Flow").innerHTML).toBe(initialFlow);
    expect(within(block("Configuration")).getByText("Network error")).toBeInTheDocument();
    expectMissing(fieldRow("Database name"), "Unavailable");
    const initialConfig = block("Configuration").innerHTML;
    Object.assign(data.volume, { measurement: "NOT_COLLECTED", event_count: null, average_eps: null, reason_code: "not_collected" });
    data.flow.state = "NOT_MEASURED";
    view.rerender(<JdbcDetails data={data} />);
    expect(block("Configuration").innerHTML).toBe(initialConfig);
    expectMissing(within(block("Flow")).getByText("Events (count)").parentElement!, "Not collected");
  });

  it("no credentials or HTML", () => {
    const data = source();
    const markup = "<script>alert(1)</script><b>billing</b>";
    data.config.safe_fields!.database_name = markup;
    const secret = "SENTINEL-SECRET";
    Object.assign(data, { password: secret, username: secret, connection_string: secret });
    Object.assign(data.config, { remote_message: secret });
    Object.assign(data.config.safe_fields!, { password: secret, username: secret, jdbc_url: secret });
    Object.assign(data.qradar, { message: secret });
    const { container } = render(<JdbcDetails data={data} />);
    expect(within(fieldRow("Database name")).getByText(markup)).toBeInTheDocument();
    expect(container.querySelector("script,b,iframe,input")).toBeNull();
    expect(container.textContent).not.toContain(secret);
    expect(container.textContent).not.toMatch(/password|username|connection.string|jdbc.url/i);
  });

  it("no credentials or HTML: unknown codes never echo remote text", () => {
    const data = source();
    const secret = "SENTINEL-SECRET";
    Object.assign(data.config, { state: secret, support: secret, failure_code: secret });
    Object.assign(data.qradar, { status: secret, inconsistency: secret, reason_code: secret, inventory_state: secret });
    Object.assign(data.flow, { state: secret, reason_code: secret });
    Object.assign(data.volume, { measurement: secret, reason_code: secret });
    Object.assign(data.config.field_states.table_name!, { code: secret, reason: secret });
    const { container } = render(<JdbcDetails data={data} />);
    expect(container.textContent).not.toContain(secret);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(5);
  });

  it("wraps long host, database and select-list values while preserving clocks and numbers", () => {
    const data = source();
    data.config.safe_fields!.database_host = "db".repeat(200);
    data.config.safe_fields!.database_name = "database".repeat(200);
    data.config.safe_fields!.select_list = "column_name, ".repeat(200);
    render(<JdbcDetails data={data} />);
    for (const [name, value] of [["Database host", data.config.safe_fields!.database_host],
      ["Database name", data.config.safe_fields!.database_name], ["Select list", data.config.safe_fields!.select_list]]) {
      expect(fieldRow(name!).querySelector("dd")!.textContent).toBe(value);
      expect(fieldRow(name!).closest("dl")).toHaveClass("jdbc-facts");
    }
    expect(within(fieldRow("Database port")).getByText("1433")).toHaveClass("nowrap", "num");
    const css = readFileSync("src/app/log-sources/log-sources.css", "utf8");
    expect(css).toMatch(/\.jdbc-facts dd\s*\{[^}]*overflow-wrap: break-word;/);
    expect(css).not.toMatch(/anywhere|break-all/);
    expect(css).toMatch(/\.jdbc-facts > div\s*\{[^}]*minmax\(0, 1fr\) minmax\(0, 2fr\)/);
  });

  it("native disclosure keyboard", () => {
    const { container } = render(<JdbcDetails data={source()} />);
    const summaries = Array.from(container.querySelectorAll("summary"));
    expect(summaries).toHaveLength(4);
    expect(summaries.map((summary) => summary.textContent)).toEqual([
      "Advanced configuration", "Configuration details", "Connection details", "Flow details",
    ]);
    for (const summary of summaries) {
      const details = summary.parentElement as HTMLDetailsElement;
      expect(details.tagName).toBe("DETAILS");
      expect(details.firstElementChild).toBe(summary);
      expect(summary).not.toHaveAttribute("role");
      expect(summary).not.toHaveAttribute("aria-expanded");
      expect(summary).not.toHaveAttribute("tabindex", "-1");
      summary.focus();
      expect(summary).toHaveFocus();
      expect(details.open).toBe(false);
      summary.click();
      expect(details.open).toBe(true);
      summary.click();
      expect(details.open).toBe(false);
    }
  });
});
