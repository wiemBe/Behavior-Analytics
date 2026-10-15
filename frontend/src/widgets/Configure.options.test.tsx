// Config options that come from a read (UI-3.5b): the drawer requests the read once, narrows a
// long list in the browser only, designs the read's own states, and stores the id.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigureDrawer, FILTER_AFTER, setConfigKey } from "./Configure";
import { widgetDef } from "./registry";
import type { AnyWidgetDef, LayoutWidget } from "./types";

const def = widgetDef("waf.endpoint_traffic") as AnyWidgetDef;
const sourceDef = widgetDef("chart.eps_observed_expected") as AnyWidgetDef;

const wafSource = (id: string, name: string) => ({ log_source_id: id, log_source_name: name });
const endpoint = (id: string, path: string, host: string | null = null) => ({
  id,
  normalized_endpoint: path,
  http_method: "GET",
  virtual_host: host,
  is_active: true,
});

interface Routes {
  [path: string]: { status?: number; body?: unknown; pending?: boolean };
}
const asked: string[] = [];
let routes: Routes = {};

beforeEach(() => {
  asked.length = 0;
  routes = {};
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
    const path = String(input).replace(/^.*\/api\/v1/, "");
    asked.push(path);
    const route = routes[path];
    if (!route) return Promise.resolve(new Response("{}", { status: 404 }));
    if (route.pending) return new Promise<Response>(() => {});
    return Promise.resolve(new Response(JSON.stringify(route.body ?? {}), { status: route.status ?? 200 }));
  }) as unknown as typeof fetch;
});

const SOURCES = "/behavior/waf/sources?limit=200";
const ENDPOINTS = (id: string) => `/behavior/waf/sources/${id}/endpoints?limit=200`;

const placed = (config: Record<string, unknown> = {}): LayoutWidget => ({
  id: "w_test1",
  type: def.type,
  x: 0,
  y: 0,
  w: 6,
  h: 14,
  range: null,
  config: { ...def.defaultConfig, ...config },
});

function open(widget = placed(), d: AnyWidgetDef = def) {
  const onApply = vi.fn();
  const onClose = vi.fn();
  render(
    <ConfigureDrawer def={d} widget={widget} dashboardRange="24h" onApply={onApply} onClose={onClose} />,
  );
  return { onApply, onClose };
}

const apply = () => screen.getByRole("button", { name: "Apply" });
const choose = (name: string, value: string) =>
  fireEvent.change(screen.getByLabelText(name), { target: { value } });

function twoSources() {
  routes[SOURCES] = {
    body: { items: [wafSource("s2", "waf-b"), wafSource("s1", "waf-a")], total: 2, limit: 200, offset: 0 },
  };
}

describe("an option source", () => {
  it("requests its read once, lists labelled ids sorted by label, and waits for a choice", async () => {
    twoSources();
    open();
    const select = await screen.findByRole("combobox", { name: "WAF source" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose…",
      "waf-a",
      "waf-b",
    ]);
    expect(asked).toEqual([SOURCES]);
    expect(apply()).toBeDisabled();
  });

  it("the endpoint picker waits for a source, then reads that source's endpoints once", async () => {
    twoSources();
    routes[ENDPOINTS("s1")] = {
      body: { items: [endpoint("e1", "/b"), endpoint("e2", "/a", "h.example")], total: 2, limit: 200, offset: 0 },
    };
    open();
    await screen.findByRole("combobox", { name: "WAF source" });
    const before = screen.getByRole("combobox", { name: "Endpoint" });
    expect(before).toBeDisabled();
    expect(before).toHaveDisplayValue("Choose a WAF source first");
    expect(asked).toEqual([SOURCES]);

    choose("WAF source", "s1");
    const endpoints = await screen.findByRole("combobox", { name: "Endpoint" });
    await waitFor(() => expect(endpoints).toBeEnabled());
    expect(within(endpoints).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose…",
      "GET /a · h.example",
      "GET /b",
    ]);
    expect(asked).toEqual([SOURCES, ENDPOINTS("s1")]);
  });

  it("stores the ids only, and Apply is on once both are chosen", async () => {
    twoSources();
    routes[ENDPOINTS("s1")] = {
      body: { items: [endpoint("e1", "/login")], total: 1, limit: 200, offset: 0 },
    };
    const { onApply } = open();
    await screen.findByRole("combobox", { name: "WAF source" });
    choose("WAF source", "s1");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Endpoint" })).toBeEnabled());
    expect(apply()).toBeDisabled();
    choose("Endpoint", "e1");
    expect(apply()).toBeEnabled();
    fireEvent.click(apply());
    expect(onApply).toHaveBeenCalledWith({
      range: null,
      config: { waf_source: "s1", endpoint: "e1" },
    });
  });

  it("changing the source clears the endpoint, and Apply goes off again", async () => {
    twoSources();
    routes[ENDPOINTS("s1")] = { body: { items: [endpoint("e1", "/a")], total: 1, limit: 200, offset: 0 } };
    routes[ENDPOINTS("s2")] = { body: { items: [endpoint("e9", "/z")], total: 1, limit: 200, offset: 0 } };
    open(placed({ waf_source: "s1", endpoint: "e1" }));
    await waitFor(() => expect(apply()).toBeEnabled());
    choose("WAF source", "s2");
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Endpoint" })).toHaveDisplayValue("Choose…"));
    expect(apply()).toBeDisabled();
  });

  it("setConfigKey clears the dependants and nothing else", () => {
    expect(
      setConfigKey(def.config, { waf_source: "s1", endpoint: "e1" }, "waf_source", "s2"),
    ).toEqual({ waf_source: "s2", endpoint: null });
    expect(
      setConfigKey(def.config, { waf_source: "s1", endpoint: "e1" }, "endpoint", "e2"),
    ).toEqual({ waf_source: "s1", endpoint: "e2" });
  });

  it("a stored id the read no longer returns is not a choice: Apply stays off", async () => {
    twoSources();
    open(placed({ waf_source: "gone", endpoint: null }));
    const select = await screen.findByRole("combobox", { name: "WAF source" });
    expect(select).toHaveDisplayValue("Choose…");
    expect(apply()).toBeDisabled();
  });
});

describe("the filter over a long list", () => {
  const many = Array.from({ length: FILTER_AFTER + 4 }, (_, i) =>
    endpoint(`e${i}`, i === 3 ? "/portal/login" : `/page/${i}`),
  );

  async function longList() {
    twoSources();
    routes[ENDPOINTS("s1")] = { body: { items: many, total: many.length, limit: 200, offset: 0 } };
    const view = open(placed({ waf_source: "s1" }));
    await screen.findByRole("searchbox", { name: "Filter endpoints" });
    return view;
  }

  it("narrows the options in the browser and never reaches a request", async () => {
    await longList();
    const requests = [...asked];
    // A long list is a listbox: the options are in view, with no placeholder row.
    const select = screen.getByRole("listbox", { name: "Endpoint" });
    expect(within(select).getAllByRole("option")).toHaveLength(many.length);
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter endpoints" }), {
      target: { value: "Login" },
    });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "GET /portal/login",
    ]);
    expect(screen.getByText("1 of 12 shown")).toBeInTheDocument();
    // No request was made for the text, and no URL anywhere holds it.
    expect(asked).toEqual(requests);
    expect(asked.join(" ").toLowerCase()).not.toContain("login");
  });

  it("is not part of the stored config, and the chosen option stays when the filter hides it", async () => {
    const { onApply } = await longList();
    choose("Endpoint", "e5");
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter endpoints" }), {
      target: { value: "login" },
    });
    expect(screen.getByRole("listbox", { name: "Endpoint" })).toHaveDisplayValue("GET /page/5");
    fireEvent.click(apply());
    expect(onApply).toHaveBeenCalledWith({ range: null, config: { waf_source: "s1", endpoint: "e5" } });
    expect(JSON.stringify(onApply.mock.calls)).not.toContain("login");
  });

  it("appears only when the list is long", async () => {
    twoSources();
    routes[ENDPOINTS("s1")] = {
      body: { items: many.slice(0, FILTER_AFTER), total: FILTER_AFTER, limit: 200, offset: 0 },
    };
    open(placed({ waf_source: "s1" }));
    await screen.findByRole("combobox", { name: "Endpoint" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Endpoint" })).toBeEnabled());
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("says when the read returned only the first page", async () => {
    twoSources();
    routes[ENDPOINTS("s1")] = { body: { items: many, total: 450, limit: 200, offset: 0 } };
    open(placed({ waf_source: "s1" }));
    expect(await screen.findByText(/first 12 of 450 endpoints/)).toBeInTheDocument();
  });
});

describe("the read's own states", () => {
  it("loading: says so and Apply is off", () => {
    routes[SOURCES] = { pending: true };
    open();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the WAF sources…");
    expect(screen.queryByRole("combobox", { name: "WAF source" })).toBeNull();
    expect(apply()).toBeDisabled();
  });

  it("failed: says what failed and what to do, with the status, and Apply is off", async () => {
    routes[SOURCES] = { status: 500 };
    open();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The WAF sources could not be read.");
    expect(alert).toHaveTextContent("HTTP 500");
    expect(alert).toHaveTextContent("open it again");
    expect(apply()).toBeDisabled();
  });

  it("forbidden: names the cause and Apply is off", async () => {
    routes[SOURCES] = { status: 403 };
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your role cannot read the WAF sources");
    expect(apply()).toBeDisabled();
  });

  it("empty: says what is empty and why, and Apply is off", async () => {
    routes[SOURCES] = { body: { items: [], total: 0, limit: 200, offset: 0 } };
    open();
    const state = await screen.findByText("No WAF sources.");
    expect(state.parentElement).toHaveTextContent("No WAF source has a behavior profile yet");
    expect(apply()).toBeDisabled();
  });

  it("a source with no endpoints is empty too", async () => {
    twoSources();
    routes[ENDPOINTS("s1")] = { body: { items: [], total: 0, limit: 200, offset: 0 } };
    open(placed({ waf_source: "s1" }));
    expect(await screen.findByText("No endpoints.")).toBeInTheDocument();
    expect(apply()).toBeDisabled();
  });
});

describe("the monitored log sources", () => {
  it("lists every source from the plain list, and stores its id", async () => {
    routes["/behavior/sources"] = {
      body: [
        { log_source_id: "a", name: "fw-edge-01" },
        { log_source_id: "b", name: "dc-win-02" },
      ],
    };
    const { onApply } = open(
      { ...placed(), type: sourceDef.type, config: { source: null } },
      sourceDef,
    );
    const select = await screen.findByRole("combobox", { name: "Log source" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose…",
      "dc-win-02",
      "fw-edge-01",
    ]);
    expect(asked).toEqual(["/behavior/sources"]);
    choose("Log source", "b");
    fireEvent.click(apply());
    expect(onApply).toHaveBeenCalledWith({ range: null, config: { source: "b" } });
  });
});
