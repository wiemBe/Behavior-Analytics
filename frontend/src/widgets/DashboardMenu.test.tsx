import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import {
  DashboardMenu,
  dashboardAccess,
  nameProblem,
  type Caller,
  type CurrentDashboard,
  type DashboardChange,
} from "./DashboardMenu";
import { SOC_OVERVIEW } from "./templates";

const api = vi.hoisted(() => ({
  createDashboard: vi.fn(),
  deleteDashboard: vi.fn(),
  duplicateDashboard: vi.fn(),
  patchDashboard: vi.fn(),
  setDefaultDashboard: vi.fn(),
}));

vi.mock("@/lib/dashboardApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/dashboardApi")>()),
  ...api,
}));

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

const caller = (...permissions: string[]): Caller => ({
  status: "ok",
  subject: "me",
  permissions: new Set(permissions),
});
const WRITE = caller("dashboard:write", "read:dashboards");
const ADMIN = caller("dashboard:write", "admin:dashboards");

const mine = (over: Partial<Extract<CurrentDashboard, { kind: "saved" }>> = {}): CurrentDashboard => ({
  kind: "saved",
  id: "d1",
  name: "Night shift",
  ownerSubject: "me",
  visibility: "PRIVATE",
  isDefault: false,
  ...over,
});
const template: CurrentDashboard = { kind: "template" };

const dashboardCss = readFileSync(resolve(__dirname, "dashboard.css"), "utf8");

describe("dashboardAccess", () => {
  it("offers every action on an owned dashboard, and sharing only with admin:dashboards", () => {
    expect(dashboardAccess(mine(), WRITE).actions).toEqual([
      "rename",
      "duplicate",
      "default",
      "new",
      "delete",
    ]);
    expect(dashboardAccess(mine(), ADMIN).actions).toEqual([
      "rename",
      "duplicate",
      "default",
      "visibility",
      "new",
      "delete",
    ]);
  });

  it("offers only 'new from template' on the template", () => {
    expect(dashboardAccess(template, WRITE)).toEqual({ actions: ["new"], note: null });
  });

  it("offers only a duplicate on a dashboard another person owns, and names the owner", () => {
    const access = dashboardAccess(mine({ ownerSubject: "alice", visibility: "SHARED" }), ADMIN);
    expect(access.actions).toEqual(["duplicate"]);
    expect(access.note).toMatch(/Shared by alice/);
  });

  it("offers nothing without dashboard:write, and says which permission", () => {
    const access = dashboardAccess(mine(), caller("read:dashboards", "admin:dashboards"));
    expect(access.actions).toEqual([]);
    expect(access.note).toMatch(/cannot save dashboards.*dashboard:write/);
    expect(dashboardAccess(template, caller()).actions).toEqual([]);
  });

  it("fails closed when /me failed, with the reason, and says nothing while it loads", () => {
    const failed = dashboardAccess(mine(), { status: "failed", reason: "HTTP 503" });
    expect(failed.actions).toEqual([]);
    expect(failed.note).toMatch(/could not be read \(HTTP 503\).*read-only/);
    expect(dashboardAccess(mine(), { status: "loading" })).toEqual({ actions: [], note: null });
  });

  it("offers nothing after the server refused a write, and says what it said", () => {
    const access = dashboardAccess(mine(), ADMIN, "only the owner can change this dashboard.");
    expect(access.actions).toEqual([]);
    expect(access.note).toMatch(/refused the change: only the owner/);
  });
});

describe("nameProblem", () => {
  it("accepts 1 to 80 characters after trimming", () => {
    expect(nameProblem("  x  ")).toBeNull();
    expect(nameProblem("a".repeat(80))).toBeNull();
    expect(nameProblem("   ")).toMatch(/Enter a name/);
    expect(nameProblem("a".repeat(81))).toMatch(/at most 80/);
  });
});

describe("dashboard dialog fields", () => {
  it("gives every dialog input and select the token-only admin-field treatment and focus ring", () => {
    const fields = dashboardCss.match(
      /\.dg-modal-field input,\s*\.dg-modal-field select\s*\{([^}]*)\}/,
    )?.[1];
    expect(fields).toBeDefined();
    expect(fields).toContain("height: var(--dash-field-h)");
    expect(fields).toContain("padding: var(--sp-2) var(--sp-3)");
    expect(fields).toContain("color: var(--text)");
    expect(fields).toContain("background: var(--surface-2)");
    expect(fields).toContain("border: 1px solid var(--border-strong)");
    expect(fields).toContain("border-radius: var(--r-md)");
    expect(fields).toContain("font-size: var(--fs-sm)");
    expect(fields).toContain("line-height: var(--lh-base)");
    expect(fields).not.toMatch(/#[0-9a-f]{3,8}|rgba?\(/i);

    const focus = dashboardCss.match(
      /\.dg-modal-field input:focus-visible,\s*\.dg-modal-field select:focus-visible\s*\{([^}]*)\}/,
    )?.[1];
    expect(focus).toContain("outline: 2px solid var(--focus)");
    expect(focus).toContain("outline-offset: 2px");
  });
});

function setup(
  current: CurrentDashboard,
  caller: Caller = WRITE,
  refused: string | null = null,
  extra: Partial<Parameters<typeof DashboardMenu>[0]> = {},
) {
  const onChanged = vi.fn<(change: DashboardChange) => void>();
  const onRefused = vi.fn<(detail: string) => void>();
  const view = render(
    <DashboardMenu
      current={current}
      access={dashboardAccess(current, caller, refused)}
      onChanged={onChanged}
      onRefused={onRefused}
      {...extra}
    />,
  );
  return { onChanged, onRefused, ...view };
}

const open = async () => userEvent.click(screen.getByRole("button", { name: "Manage" }));
const choose = async (name: string | RegExp) => {
  await open();
  await userEvent.click(screen.getByRole("button", { name }));
};

describe("DashboardMenu controls", () => {
  it("renders no control at all when the role cannot write, when /me failed, and while it loads", () => {
    for (const c of [caller("read:dashboards"), { status: "failed", reason: "HTTP 500" } as Caller, { status: "loading" } as Caller]) {
      const { container, unmount } = setup(mine(), c);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  it("renders no control after a 403 has been recorded", () => {
    const { container } = setup(mine(), ADMIN, "refused");
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the owned dashboard's actions, with the share entry only for admin:dashboards", async () => {
    const { unmount } = setup(mine(), WRITE);
    await open();
    const names = () =>
      screen.getAllByRole("button", { name: /.+/ }).map((b) => b.textContent);
    expect(names()).toEqual([
      "Manage",
      "Rename…",
      "Duplicate",
      "Set as default",
      "New dashboard from template…",
      "Delete…",
    ]);
    unmount();
    setup(mine({ visibility: "SHARED", isDefault: true }), ADMIN);
    await open();
    expect(screen.getByRole("button", { name: "Clear default" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Make private" })).toBeInTheDocument();
  });

  it("offers 'Duplicate into my dashboards' alone on a shared dashboard another person owns", async () => {
    setup(mine({ ownerSubject: "alice", visibility: "SHARED" }), ADMIN);
    await open();
    expect(
      screen.getAllByRole("button").map((b) => b.textContent),
    ).toEqual(["Manage", "Duplicate into my dashboards"]);
  });

  it("offers only 'New dashboard from template' on the template", async () => {
    setup(template, WRITE);
    await open();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Manage",
      "New dashboard from template…",
    ]);
  });

  it("restores a known template's current version only after confirmation", async () => {
    const onResetLayout = vi.fn();
    setup(mine({ templateKey: "soc_overview" }), WRITE, null, {
      customizing: true,
      onResetLayout,
    });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Restore defaults…" }));
    expect(screen.getByRole("dialog", { name: "Restore SOC Overview defaults?" })).toHaveTextContent(
      "SOC Overview version 1",
    );
    expect(onResetLayout).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Restore defaults" }));
    expect(onResetLayout).toHaveBeenCalledWith(SOC_OVERVIEW.layout);
  });

  it("resets a dashboard without a template to empty only after confirmation", async () => {
    const onResetLayout = vi.fn();
    setup(mine({ templateKey: null }), WRITE, null, { customizing: true, onResetLayout });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Reset to empty…" }));
    expect(screen.getByRole("dialog", { name: 'Reset "Night shift" to empty?' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reset to empty" }));
    expect(onResetLayout).toHaveBeenCalledWith({ v: 1, range: "24h", widgets: [] });
  });

  it("offers neither reset action for an unknown template key and says why", async () => {
    setup(mine({ templateKey: "future_template" }), WRITE, null, { customizing: true });
    await open();
    expect(screen.queryByRole("button", { name: /Restore defaults|Reset to empty/ })).toBeNull();
    expect(screen.getByText(/does not know template future_template/)).toBeInTheDocument();
  });
});

describe("DashboardMenu writes", () => {
  it("creates from the template with its key and version, then goes to the new dashboard", async () => {
    api.createDashboard.mockResolvedValue({ id: "new1" });
    const { onChanged } = setup(template);
    await choose(/New dashboard from template/);
    const dialog = screen.getByRole("dialog", { name: "New dashboard from template" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByLabelText("Template")).toHaveValue("soc_overview");
    await userEvent.type(screen.getByLabelText("Name"), "  Day shift  ");
    await userEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ go: { kind: "saved", id: "new1" } }));
    expect(api.createDashboard).toHaveBeenCalledWith({
      name: "Day shift",
      layout: SOC_OVERVIEW.layout,
      template_key: "soc_overview",
      template_version: 1,
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("refuses an empty or over-long name before sending anything", async () => {
    setup(template);
    await choose(/New dashboard from template/);
    await userEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a name.");
    expect(api.createDashboard).not.toHaveBeenCalled();
  });

  it("renames with a PATCH and re-reads without moving", async () => {
    api.patchDashboard.mockResolvedValue({});
    const { onChanged } = setup(mine());
    await choose("Rename…");
    const input = screen.getByLabelText("Name");
    expect(input).toHaveValue("Night shift");
    await userEvent.clear(input);
    await userEvent.type(input, "Day shift");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ go: null }));
    expect(api.patchDashboard).toHaveBeenCalledWith("d1", { name: "Day shift" });
  });

  it("keeps the dialog open and shows the server's text on a duplicate name (409)", async () => {
    api.patchDashboard.mockRejectedValue(
      new ApiError(409, "you already have a dashboard named 'Day shift'", "you already have a dashboard named 'Day shift'"),
    );
    const { onChanged } = setup(mine());
    await choose("Rename…");
    const input = screen.getByLabelText("Name");
    await userEvent.clear(input);
    await userEvent.type(input, "Day shift");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You already have a dashboard named 'Day shift'.",
    );
    expect(screen.getByRole("dialog", { name: "Rename dashboard" })).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("shows the server's validation message on a 422 and the 20-dashboard limit on creation", async () => {
    api.createDashboard
      .mockRejectedValueOnce(
        new ApiError(422, "Unprocessable Entity", [{ msg: "Value error, name cannot be null" }]),
      )
      .mockRejectedValueOnce(
        new ApiError(409, "the limit is 20 dashboards per owner", "the limit is 20 dashboards per owner"),
      );
    setup(template);
    await choose(/New dashboard from template/);
    await userEvent.type(screen.getByLabelText("Name"), "X");
    await userEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Name cannot be null.");
    await userEvent.click(screen.getByRole("button", { name: "Create dashboard" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("The limit is 20 dashboards per owner."),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("duplicates and goes to the copy", async () => {
    api.duplicateDashboard.mockResolvedValue({ id: "copy1" });
    const { onChanged } = setup(mine({ ownerSubject: "alice", visibility: "SHARED" }));
    await choose("Duplicate into my dashboards");
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ go: { kind: "saved", id: "copy1" } }));
    expect(api.duplicateDashboard).toHaveBeenCalledWith("d1");
  });

  it("says what the server said when a duplicate is refused, in a dialog", async () => {
    api.duplicateDashboard.mockRejectedValue(
      new ApiError(409, "the limit is 20 dashboards per owner", "the limit is 20 dashboards per owner"),
    );
    const { onChanged } = setup(mine());
    await choose("Duplicate");
    expect(await screen.findByRole("alert")).toHaveTextContent("The limit is 20 dashboards per owner.");
    expect(screen.getByRole("dialog", { name: /could not be duplicated/ })).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Manage" })).toHaveFocus();
  });

  it("asks for confirmation naming the dashboard, and sends nothing until confirmed", async () => {
    api.deleteDashboard.mockResolvedValue(undefined);
    const { onChanged } = setup(mine({ isDefault: true }));
    await choose("Delete…");
    expect(screen.getByRole("dialog", { name: 'Delete "Night shift"?' })).toBeInTheDocument();
    expect(screen.getByText(/It is your default/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(api.deleteDashboard).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Delete dashboard" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ go: "start" }));
    expect(api.deleteDashboard).toHaveBeenCalledWith("d1");
  });

  it("cancelling the delete confirm deletes nothing and returns focus to the menu button", async () => {
    setup(mine());
    await choose("Delete…");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(api.deleteDashboard).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Manage" })).toHaveFocus());
  });

  it("sets and clears the default through PUT /default", async () => {
    api.setDefaultDashboard.mockResolvedValue({});
    const first = setup(mine());
    await choose("Set as default");
    await waitFor(() => expect(first.onChanged).toHaveBeenCalledWith({ go: null }));
    expect(api.setDefaultDashboard).toHaveBeenLastCalledWith("d1");
    first.unmount();
    const second = setup(mine({ isDefault: true }));
    await choose("Clear default");
    await waitFor(() => expect(second.onChanged).toHaveBeenCalled());
    expect(api.setDefaultDashboard).toHaveBeenLastCalledWith(null);
  });

  it("shares and makes private through PATCH visibility", async () => {
    api.patchDashboard.mockResolvedValue({});
    const first = setup(mine(), ADMIN);
    await choose("Share with everyone");
    await waitFor(() => expect(first.onChanged).toHaveBeenCalled());
    expect(api.patchDashboard).toHaveBeenLastCalledWith("d1", { visibility: "SHARED" });
    first.unmount();
    const second = setup(mine({ visibility: "SHARED" }), ADMIN);
    await choose("Make private");
    await waitFor(() => expect(second.onChanged).toHaveBeenCalled());
    expect(api.patchDashboard).toHaveBeenLastCalledWith("d1", { visibility: "PRIVATE" });
  });

  it("reports a 403 to the page instead of retrying, and closes the dialog", async () => {
    api.patchDashboard.mockRejectedValue(
      new ApiError(403, "only the owner or an administrator can change this dashboard", "only the owner or an administrator can change this dashboard"),
    );
    const { onRefused, onChanged } = setup(mine());
    await choose("Rename…");
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Other");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() =>
      expect(onRefused).toHaveBeenCalledWith(
        "Only the owner or an administrator can change this dashboard.",
      ),
    );
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes the menu on Esc and returns focus to its button", async () => {
    setup(mine());
    await open();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Manage" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Manage" })).toHaveFocus();
  });
});
