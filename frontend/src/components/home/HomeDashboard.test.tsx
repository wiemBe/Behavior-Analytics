import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeDashboard } from "./HomeDashboard";

const mocks = vi.hoisted(() => ({
  search: "",
  push: vi.fn(),
  realView: false,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/components/shell/AutoRefresh", () => ({
  formatClockUtc: () => "13:58:00 UTC",
  useRefreshTick: () => 0,
  useReportDataAge: vi.fn(),
}));
vi.mock("@/widgets/DashboardView", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/widgets/DashboardView")>();
  return {
    DashboardView: (props: Parameters<typeof actual.DashboardView>[0]) =>
      mocks.realView ? (
        <actual.DashboardView {...props} />
      ) : (
        <>
          {props.renderHeader(
            <>
              {typeof props.leading === "function" ? props.leading(false) : props.leading}
              <button type="button">Customize</button>
            </>,
          )}
          <div data-testid="view" data-saved={String(props.saved ?? false)}>
            {props.layout.widgets.map((w) => w.id).join(",")}
          </div>
        </>
      ),
  };
});

const layoutOf = (...ids: string[]) => ({
  v: 1,
  range: "24h",
  widgets: ids.map((id) => ({ id, type: "kpi.total_eps", x: 0, y: 0, w: 3, h: 7, range: null, config: {} })),
});

interface Row {
  id: string;
  name: string;
  owner: string;
  visibility: "PRIVATE" | "SHARED";
  isDefault: boolean;
  layout: unknown;
  description?: string | null;
  templateKey?: string | null;
}

const rowsInit = (): Row[] => [
  { id: "d1", name: "Night shift", owner: "me", visibility: "PRIVATE", isDefault: false, layout: layoutOf("w_aaaa") },
  { id: "d2", name: "Day shift", owner: "me", visibility: "PRIVATE", isDefault: true, layout: layoutOf("w_bbbb", "w_cccc"), description: "What the day team watches." },
  { id: "d3", name: "WAF watch", owner: "alice", visibility: "SHARED", isDefault: false, layout: layoutOf("w_dddd") },
];

let rows: Row[];
let me: { status: number; body: unknown };
let listStatus: number;
let writeResponse: { status: number; body: unknown } | null;
let writeResponses: Array<{ status: number; body: unknown }>;
let networkPutFailure: boolean;
let calls: Array<{ method: string; path: string; body?: unknown }>;

const WRITER = {
  subject: "me",
  email: null,
  permissions: [],
  effective_permissions: ["read:dashboards", "dashboard:write"],
};

const out = (r: Row) => ({
  id: r.id,
  name: r.name,
  description: r.description ?? null,
  owner_subject: r.owner,
  visibility: r.visibility,
  template_key: r.templateKey ?? null,
  template_version: null,
  schema_version: 1,
  layout: r.layout,
  revision: 1,
  created_at: "2026-10-14T10:00:00Z",
  updated_at: "2026-10-14T10:00:00Z",
  updated_by: r.owner,
  is_default: r.isDefault,
});

function json(status: number, body: unknown) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status });
}

beforeEach(() => {
  rows = rowsInit();
  me = { status: 200, body: WRITER };
  listStatus = 200;
  writeResponse = null;
  writeResponses = [];
  networkPutFailure = false;
  calls = [];
  mocks.search = "";
  mocks.realView = false;
  mocks.push.mockReset();
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = String(input).replace(/^.*\/api\/v1/, "");
      const method = init?.method ?? "GET";
      calls.push({ method, path, body: init?.body ? JSON.parse(init.body as string) : undefined });
      if (path === "/me") return json(me.status, me.body);
      if (method === "PUT" && /^\/dashboards\/[^/]+$/.test(path) && networkPutFailure) {
        throw new TypeError("network unavailable");
      }
      const queued = method !== "GET" ? writeResponses.shift() : null;
      if (method !== "GET" && (queued || writeResponse)) {
        const response = queued ?? writeResponse!;
        if (response.status < 300) {
          // a write that the fake server accepted: apply the one change these tests make
          const id = path.split("/")[2];
          const row = rows.find((r) => r.id === id);
          const body = init?.body ? JSON.parse(init.body as string) : {};
          if (method === "PATCH" && row && body.name) row.name = body.name;
          if (method === "PUT" && row && body.layout) row.layout = body.layout;
        }
        return json(response.status, response.body);
      }
      if (path.startsWith("/dashboards?")) {
        if (listStatus !== 200) return json(listStatus, { detail: "unavailable" });
        return json(200, {
          items: rows.map((r) => ({
            id: r.id,
            name: r.name,
            owner_subject: r.owner,
            visibility: r.visibility,
            template_key: r.templateKey ?? null,
            updated_at: "2026-10-14T10:00:00Z",
            widget_count: (r.layout as { widgets?: unknown[] }).widgets?.length ?? 0,
            is_default: r.isDefault,
          })),
          total: rows.length,
          limit: 200,
          offset: 0,
        });
      }
      const match = /^\/dashboards\/([^/]+)$/.exec(path);
      if (match && method === "GET") {
        const row = rows.find((r) => r.id === match[1]);
        return row ? json(200, out(row)) : json(404, { detail: "dashboard not found" });
      }
      return json(404, { detail: "not in the fake server" });
    }),
  );
});

const listReads = () => calls.filter((c) => c.method === "GET" && c.path.startsWith("/dashboards?"));
const view = () => screen.findByTestId("view");
const h1 = () => screen.getByRole("heading", { level: 1 });

const rangedLayout = () => ({
  v: 1,
  range: "24h",
  widgets: [
    {
      id: "w_chart",
      type: "chart.eps_fleet_total",
      x: 0,
      y: 0,
      w: 8,
      h: 12,
      range: null,
      config: {},
    },
  ],
});

async function makeRangeDirty() {
  const group = await screen.findByRole("group", { name: "Window" });
  await userEvent.click(within(group).getByRole("button", { name: "1 h" }));
  expect(screen.getByText(/Unsaved changes/)).toBeInTheDocument();
}

describe("which dashboard `/` shows", () => {
  it("shows the dashboard marked default, with its name, description and layout", async () => {
    render(<HomeDashboard />);
    expect(await view()).toHaveTextContent("w_bbbb,w_cccc");
    expect(h1()).toHaveTextContent("Day shift");
    expect(screen.getByText("What the day team watches.")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(screen.getByTestId("view")).toHaveAttribute("data-saved", "true");
    expect(screen.queryByText("Template, not saved")).toBeNull();
    // the default's own read, not the others'
    expect(calls.filter((c) => /^\/dashboards\/d\d$/.test(c.path)).map((c) => c.path)).toEqual([
      "/dashboards/d2",
    ]);
  });

  it("shows ?dashboard= over the default", async () => {
    mocks.search = "dashboard=d1";
    render(<HomeDashboard />);
    expect(await view()).toHaveTextContent("w_aaaa");
    expect(h1()).toHaveTextContent("Night shift");
    expect(screen.queryByText("Default")).toBeNull();
  });

  it("shows a shared dashboard another person owns as read-only, with its owner named", async () => {
    mocks.search = "dashboard=d3";
    render(<HomeDashboard />);
    await view();
    expect(h1()).toHaveTextContent("WAF watch");
    expect(screen.getByText("Shared")).toBeInTheDocument();
    expect(screen.getByText(/Shared by alice/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(
      expect.arrayContaining(["Duplicate into my dashboards"]),
    );
    expect(screen.queryByRole("button", { name: "Delete…" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Rename…" })).toBeNull();
  });

  it("shows the SOC Overview template, unsaved, when there are no dashboards", async () => {
    rows = [];
    render(<HomeDashboard />);
    expect(await view()).toHaveTextContent("w_kpieps");
    expect(h1()).toHaveTextContent("SOC Overview");
    const qualifier = screen.getByText("Template, not saved");
    expect(qualifier).toBeInTheDocument();
    const qualifierRow = qualifier.closest(".page-meta");
    expect(qualifierRow).toHaveTextContent(/Template, not saved24 h view of fleet volume/);
    expect(qualifierRow?.children).toHaveLength(2);
    expect(screen.getByText(/24 h view of fleet volume/)).toHaveClass("dg-qualifier");
    expect(screen.queryByText(/Template, not saved\s*·/)).toBeNull();
    expect(screen.getByTestId("view")).toHaveAttribute("data-saved", "false");
    // nothing reads as an error
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/could not be loaded/)).toBeNull();
  });

  it("shows the template on request even when a default is set", async () => {
    mocks.search = "dashboard=template";
    render(<HomeDashboard />);
    expect(await view()).toHaveTextContent("w_kpieps");
    expect(h1()).toHaveTextContent("SOC Overview");
  });

  it("shows the template with one line, the reason and 'not saved' when the list read failed", async () => {
    listStatus = 503;
    render(<HomeDashboard />);
    expect(await view()).toHaveTextContent("w_kpieps");
    const line = screen.getByText(/Saved dashboards could not be loaded/, { selector: "p" });
    expect(line).toHaveTextContent("the server answered HTTP 503");
    expect(line).toHaveTextContent("changes here are not saved");
    expect(h1()).toHaveTextContent("SOC Overview");
  });

  it("names a role that cannot read dashboards when the list is refused", async () => {
    listStatus = 403;
    render(<HomeDashboard />);
    await view();
    expect(screen.getByText(/Saved dashboards could not be loaded/, { selector: "p" })).toHaveTextContent(
      "read:dashboards",
    );
  });

  it("says a dashboard that is not there is not there, and links to the default", async () => {
    mocks.search = "dashboard=nope";
    render(<HomeDashboard />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("does not exist, or it is not shared with you");
    expect(within(alert).getByRole("link", { name: "Open your default dashboard" })).toHaveAttribute("href", "/");
    expect(screen.queryByTestId("view")).toBeNull();
    // the switcher is still there to leave by
    expect(screen.getByRole("button", { name: "Dashboards" })).toBeInTheDocument();
  });

  it("offers the template, not a default, in the 404 message when none is set", async () => {
    rows = rowsInit().map((r) => ({ ...r, isDefault: false }));
    mocks.search = "dashboard=nope";
    render(<HomeDashboard />);
    const alert = await screen.findByRole("alert");
    await waitFor(() =>
      expect(within(alert).getByRole("link")).toHaveTextContent("Open the SOC Overview template"),
    );
  });

  it("does not guess at a layout version it does not know: names it and offers the template", async () => {
    rows[1].layout = { v: 2, range: "24h", widgets: [] };
    render(<HomeDashboard />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("version 2");
    expect(within(alert).getByRole("link", { name: "Open the SOC Overview template" })).toHaveAttribute(
      "href",
      "/?dashboard=template",
    );
    expect(screen.queryByTestId("view")).toBeNull();
    expect(h1()).toHaveTextContent("Day shift");
  });

  it("renders a widget type this version does not know as the unsupported placeholder", async () => {
    mocks.realView = true;
    rows[1].layout = {
      v: 1,
      range: "24h",
      widgets: [{ id: "w_zzzz", type: "future.widget", x: 0, y: 0, w: 4, h: 6, range: null, config: {} }],
    };
    render(<HomeDashboard />);
    expect(await screen.findByRole("heading", { name: "Unsupported widget" })).toBeInTheDocument();
    expect(screen.getByText(/widget of type future\.widget that this version does not know/)).toBeInTheDocument();
  });

  it("keeps dashboard navigation available while customizing and removes the interim no-save copy", async () => {
    mocks.realView = true;
    rows[1].layout = { v: 1, range: "24h", widgets: [] };
    const { unmount } = render(<HomeDashboard />);
    await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
    expect(screen.queryByText(/Changes are not saved|saving is not built/)).toBeNull();
    expect(screen.getByRole("button", { name: "Dashboards" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.getByRole("button", { name: "Dashboards" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Manage" })).toBeInTheDocument();
    unmount();
  });

  it("shows the header and an empty grid while the dashboards are read, then the dashboard", async () => {
    const { container } = render(<HomeDashboard />);
    expect(h1()).toHaveTextContent("Dashboard");
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(screen.queryByTestId("view")).toBeNull();
    await view();
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
  });
});

describe("the switcher on the page", () => {
  async function pick(name: RegExp) {
    await view();
    await userEvent.click(screen.getByRole("button", { name: "Dashboards" }));
    await userEvent.click(screen.getByRole("button", { name }));
  }

  it("navigates to ?dashboard=<id>", async () => {
    render(<HomeDashboard />);
    await pick(/Night shift/);
    expect(mocks.push).toHaveBeenCalledWith("/?dashboard=d1");
  });

  it("navigates to the template by its parameter when a default is set", async () => {
    render(<HomeDashboard />);
    await pick(/SOC Overview \(template\)/);
    expect(mocks.push).toHaveBeenCalledWith("/?dashboard=template");
  });

  it("navigates to / for the template when no default is set", async () => {
    rows = rowsInit().map((r) => ({ ...r, isDefault: false }));
    render(<HomeDashboard />);
    await pick(/SOC Overview \(template\)/);
    expect(mocks.push).toHaveBeenCalledWith("/");
  });

  it("lists the dashboards the list read returned and marks the one shown", async () => {
    render(<HomeDashboard />);
    await view();
    await userEvent.click(screen.getByRole("button", { name: "Dashboards" }));
    const names = within(screen.getByRole("list", { name: "Saved dashboards" }))
      .getAllByRole("button")
      .map((b) => b.querySelector(".dg-pop-name")?.textContent);
    expect(names).toEqual(["Night shift", "Day shift", "WAF watch"]);
    expect(screen.getByRole("button", { name: /Day shift/ })).toHaveAttribute("aria-current", "true");
  });
});

describe("permissions on the page", () => {
  it("renders no write control and says why when the role lacks dashboard:write", async () => {
    me = { status: 200, body: { ...WRITER, effective_permissions: ["read:dashboards"] } };
    render(<HomeDashboard />);
    await view();
    expect(await screen.findByText(/Your role cannot save dashboards/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage" })).toBeNull();
    // the switcher still works
    expect(screen.getByRole("button", { name: "Dashboards" })).toBeInTheDocument();
  });

  it("fails closed when /me failed: no write control, and the reason is shown", async () => {
    me = { status: 500, body: { detail: "boom" } };
    render(<HomeDashboard />);
    await view();
    expect(await screen.findByText(/Your permissions could not be read \(the server answered HTTP 500\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage" })).toBeNull();
  });

  it("offers no write control before /me has answered", async () => {
    render(<HomeDashboard />);
    // before anything has settled
    expect(screen.queryByRole("button", { name: "Manage" })).toBeNull();
    await view();
    expect(await screen.findByRole("button", { name: "Manage" })).toBeInTheDocument();
  });

  it("hides the write controls and shows the server's reason after a 403 on a write", async () => {
    writeResponse = { status: 403, body: { detail: "only the owner or an administrator can change this dashboard" } };
    render(<HomeDashboard />);
    await view();
    await userEvent.click(await screen.findByRole("button", { name: "Manage" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear default" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Manage" })).toBeNull());
    expect(screen.getByText(/refused the change: Only the owner or an administrator/)).toBeInTheDocument();
  });
});

describe("writes re-read the list", () => {
  it("re-reads the list after a rename and shows the new name", async () => {
    render(<HomeDashboard />);
    await view();
    await userEvent.click(await screen.findByRole("button", { name: "Manage" }));
    await userEvent.click(screen.getByRole("button", { name: "Rename…" }));
    writeResponse = { status: 200, body: {} };
    const input = screen.getByLabelText("Name");
    await userEvent.clear(input);
    await userEvent.type(input, "Evening shift");
    const before = listReads().length;
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(listReads().length).toBe(before + 1));
    expect(calls.find((c) => c.method === "PATCH")).toMatchObject({
      path: "/dashboards/d2",
      body: { name: "Evening shift" },
    });
    await waitFor(() => expect(h1()).toHaveTextContent("Evening shift"));
    // the dashboard stayed on screen: it was not replaced by the empty grid
    expect(screen.getByTestId("view")).toHaveTextContent("w_bbbb,w_cccc");
  });

  it("goes to the new dashboard after creating one", async () => {
    rows = [];
    writeResponse = { status: 201, body: { id: "new1" } };
    render(<HomeDashboard />);
    await view();
    await userEvent.click(await screen.findByRole("button", { name: "Manage" }));
    await userEvent.click(screen.getByRole("button", { name: /New dashboard from template/ }));
    await userEvent.type(screen.getByLabelText("Name"), "Fresh");
    const before = listReads().length;
    await userEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/?dashboard=new1"));
    await waitFor(() => expect(listReads().length).toBe(before + 1));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      name: "Fresh",
      template_key: "soc_overview",
      template_version: 1,
    });
  });

  it("goes back to / after a delete and does not read the deleted dashboard again", async () => {
    writeResponse = { status: 204, body: null };
    render(<HomeDashboard />);
    await view();
    await userEvent.click(await screen.findByRole("button", { name: "Manage" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete dashboard" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/"));
    expect(calls.find((c) => c.method === "DELETE")?.path).toBe("/dashboards/d2");
  });
});

describe("layout saving", () => {
  beforeEach(() => {
    mocks.realView = true;
    rows[1].layout = rangedLayout();
  });

  it("saves an outside-Customize range change with expected_revision and keeps the new revision", async () => {
    writeResponse = { status: 200, body: { ...out(rows[1]), revision: 2 } };
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved"));
    const write = calls.find((call) => call.method === "PUT" && call.path === "/dashboards/d2");
    expect(write?.body).toMatchObject({
      name: "Day shift",
      description: "What the day team watches.",
      expected_revision: 1,
      layout: { widgets: [{ id: "w_chart", range: "1h" }] },
    });
  });

  it("Discard outside Customize restores the last saved layout without a write", async () => {
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    expect(within(screen.getByRole("group", { name: "Window" })).getByRole("button", { name: "24 h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("keeps the layout and Save control after a network failure", async () => {
    networkPutFailure = true;
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Saving failed; your changes are still here.",
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Window" })).getByRole("button", { name: "1 h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("opens the two-writer dialog and Save as a copy posts the current layout", async () => {
    writeResponses = [
      {
        status: 409,
        body: { detail: { message: "dashboard revision is stale", current_revision: 2 } },
      },
      { status: 201, body: { ...out(rows[1]), id: "copy1", name: "Copy of Day shift" } },
    ];
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("dialog", {
      name: "This dashboard changed since you opened it",
    });
    // The body says what each choice does; it does not repeat the server's sentence.
    expect(dialog).toHaveTextContent("Reload discards your changes and shows the saved version.");
    expect(dialog).toHaveTextContent("Save as a copy keeps your changes under the name below.");
    expect(dialog).toHaveTextContent("Keep editing leaves both as they are.");
    expect(dialog).not.toHaveTextContent("dashboard revision is stale");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save as a copy" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/?dashboard=copy1"));
    const copy = calls.find((call) => call.method === "POST" && call.path === "/dashboards");
    expect(copy?.body).toMatchObject({
      name: "Copy of Day shift",
      layout: { widgets: [{ id: "w_chart", range: "1h" }] },
    });
  });

  it("a 403 hides Save without dropping the session layout", async () => {
    writeResponse = { status: 403, body: { detail: "only the owner can change this dashboard" } };
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).toBeNull());
    expect(screen.getAllByText(/server refused the change: Only the owner/).length).toBeGreaterThan(0);
    expect(within(screen.getByRole("group", { name: "Window" })).getByRole("button", { name: "1 h" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("never offers or sends Save without dashboard:write", async () => {
    me = { status: 200, body: { ...WRITER, effective_permissions: ["read:dashboards"] } };
    render(<HomeDashboard />);
    await makeRangeDirty();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
  });
});

describe("save refusals and the guard", () => {
  beforeEach(() => {
    mocks.realView = true;
    rows[1].layout = rangedLayout();
  });
  const stillOneHour = () =>
    expect(
      within(screen.getByRole("group", { name: "Window" })).getByRole("button", { name: "1 h" }),
    ).toHaveAttribute("aria-pressed", "true");

  it("shows a 422 detail in the header and keeps the changes and Save", async () => {
    writeResponse = { status: 422, body: { detail: "layout has too many widgets" } };
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Layout has too many widgets.");
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    stillOneHour();
  });

  it("shows a 409 limit text on a saved dashboard in the header, not as a conflict", async () => {
    writeResponse = { status: 409, body: { detail: "dashboard limit reached" } };
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Dashboard limit reached.");
    expect(screen.queryByRole("dialog")).toBeNull();
    stillOneHour();
  });

  it("Reload in the conflict dialog discards local changes and re-reads the dashboard", async () => {
    writeResponses = [
      { status: 409, body: { detail: { message: "stale", current_revision: 2 } } },
    ];
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("dialog", {
      name: "This dashboard changed since you opened it",
    });
    const reads = () => calls.filter((c) => c.method === "GET" && c.path === "/dashboards/d2").length;
    const before = reads();
    await userEvent.click(within(dialog).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
    await waitFor(() => expect(screen.queryByText(/Unsaved changes/)).toBeNull());
  });

  it("sends no write for a shared dashboard the caller does not own", async () => {
    mocks.search = "dashboard=d3";
    rows[2].layout = rangedLayout();
    render(<HomeDashboard />);
    await makeRangeDirty();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
    expect(calls.some((c) => c.method !== "GET")).toBe(false);
  });

  it("sends no write after /me failed", async () => {
    me = { status: 500, body: { detail: "boom" } };
    render(<HomeDashboard />);
    await makeRangeDirty();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(calls.some((c) => c.method !== "GET")).toBe(false);
  });

  it("asks before a menu navigation (New dashboard) when dirty", async () => {
    render(<HomeDashboard />);
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("button", { name: "Manage" }));
    await userEvent.click(screen.getByRole("button", { name: /New dashboard/ }));
    expect(screen.getByRole("dialog", { name: "Discard unsaved changes to Day shift?" })).toBeInTheDocument();
  });

  it("guards a same-tab link outside the page while dirty, and not when clean", async () => {
    render(
      <>
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- stands in for a sidebar link */}
        <a href="/rules" onClick={(event) => event.preventDefault()}>
          Rules
        </a>
        <HomeDashboard />
      </>,
    );
    await screen.findByRole("group", { name: "Window" });
    await userEvent.click(screen.getByRole("link", { name: "Rules" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await makeRangeDirty();
    await userEvent.click(screen.getByRole("link", { name: "Rules" }));
    expect(screen.getByRole("dialog", { name: "Discard unsaved changes to Day shift?" })).toBeInTheDocument();
    expect(mocks.push).not.toHaveBeenCalled();
  });
});

describe("template first save and navigation guard", () => {
  it("Done on the unsaved template creates it, sets the first default and switches to it", async () => {
    rows = [];
    mocks.realView = true;
    writeResponses = [
      { status: 201, body: { ...out(rowsInit()[0]), id: "new1", name: "SOC Overview" } },
      { status: 200, body: { dashboard_id: "new1" } },
    ];
    render(<HomeDashboard />);
    await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/?dashboard=new1"));
    expect(calls.filter((call) => call.method !== "GET").map((call) => call.path)).toEqual([
      "/dashboards",
      "/dashboards/default",
    ]);
  });

  it("a taken template name asks for another name and shows the server's text", async () => {
    rows = [];
    mocks.realView = true;
    writeResponse = {
      status: 409,
      body: { detail: "you already have a dashboard named 'SOC Overview'" },
    };
    render(<HomeDashboard />);
    await userEvent.click(await screen.findByRole("button", { name: "Customize" }));
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    const dialog = await screen.findByRole("dialog", { name: "Choose another dashboard name" });
    expect(within(dialog).getByRole("alert")).toHaveTextContent(
      "You already have a dashboard named 'SOC Overview'.",
    );
    expect(within(dialog).getByLabelText("Name")).toHaveValue("SOC Overview");
  });

  it("switching while dirty asks first; Keep editing returns focus and Discard navigates", async () => {
    mocks.realView = true;
    rows[1].layout = rangedLayout();
    render(<HomeDashboard />);
    await makeRangeDirty();
    const switcher = screen.getByRole("button", { name: "Dashboards" });
    await userEvent.click(switcher);
    await userEvent.click(screen.getByRole("button", { name: /Night shift/ }));
    const guard = screen.getByRole("dialog", { name: "Discard unsaved changes to Day shift?" });
    expect(mocks.push).not.toHaveBeenCalled();
    await userEvent.click(within(guard).getByRole("button", { name: "Keep editing" }));
    await waitFor(() => expect(switcher).toHaveFocus());

    await userEvent.click(switcher);
    await userEvent.click(screen.getByRole("button", { name: /Night shift/ }));
    const again = screen.getByRole("dialog", { name: "Discard unsaved changes to Day shift?" });
    await userEvent.click(within(again).getByRole("button", { name: "Discard" }));
    expect(mocks.push).toHaveBeenCalledWith("/?dashboard=d1");
  });

  it("clean switching does not ask", async () => {
    render(<HomeDashboard />);
    await view();
    await userEvent.click(screen.getByRole("button", { name: "Dashboards" }));
    await userEvent.click(screen.getByRole("button", { name: /Night shift/ }));
    expect(screen.queryByRole("dialog", { name: /Discard unsaved/ })).toBeNull();
    expect(mocks.push).toHaveBeenCalledWith("/?dashboard=d1");
  });

  describe("the line under the title of a dashboard made from a template (T-098)", () => {
    const admin = "Search cadence, source classification, outage policy and detection evidence health.";

    it("says what the template holds when the dashboard has no description of its own", async () => {
      rows[0].templateKey = "qradar_administration";
      mocks.search = "dashboard=d1";
      const { container } = render(<HomeDashboard />);
      await screen.findByRole("heading", { name: "Night shift" });
      expect(await screen.findByText(admin)).toBeInTheDocument();
      expect(container.querySelector(".page-meta .dg-template-desc")).not.toBeNull();
    });

    it("leaves the dashboard's own description alone, and a plain dashboard without a line", async () => {
      rows[0].templateKey = "qradar_administration";
      rows[0].description = "What the night team watches.";
      mocks.search = "dashboard=d1";
      const own = render(<HomeDashboard />);
      expect(await screen.findByText("What the night team watches.")).toBeInTheDocument();
      expect(screen.queryByText(admin)).toBeNull();
      own.unmount();

      rows[0].templateKey = null;
      rows[0].description = null;
      const plain = render(<HomeDashboard />);
      await screen.findByRole("heading", { name: "Night shift" });
      expect(plain.container.querySelector(".page-desc")).toBeNull();
    });
  });
});
