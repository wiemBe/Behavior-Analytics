import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DashboardOut } from "@/lib/dashboardApi";

import { SOC_OVERVIEW } from "./templates";
import type { DashboardLayout } from "./types";
import {
  dashboardReducer,
  initialDashboardState,
  layoutsEqual,
  useDashboard,
} from "./useDashboard";

const api = vi.hoisted(() => ({
  createDashboard: vi.fn(),
  getDashboard: vi.fn(),
  replaceDashboard: vi.fn(),
  setDefaultDashboard: vi.fn(),
}));

vi.mock("@/lib/dashboardApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/dashboardApi")>()),
  ...api,
}));

const layout = (x = 0, config: Record<string, unknown> = {}): DashboardLayout => ({
  v: 1,
  range: "24h",
  widgets: [
    {
      id: "w_aaaa",
      type: "kpi.total_eps",
      x,
      y: 0,
      w: 3,
      h: 7,
      range: null,
      config,
    },
  ],
});

const dashboard = (revision = 1, savedLayout: DashboardLayout = layout()): DashboardOut => ({
  id: "d1",
  name: "Night shift",
  description: "For the night team.",
  owner_subject: "me",
  visibility: "PRIVATE",
  template_key: "soc_overview",
  template_version: 1,
  schema_version: 1,
  layout: savedLayout,
  revision,
  created_at: "2026-10-15T10:00:00Z",
  updated_at: "2026-10-15T10:00:00Z",
  updated_by: "me",
  is_default: true,
});

const source = () => ({ kind: "saved" as const, dashboard: dashboard() });
const options = { setDefaultOnCreate: false };

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
});

describe("dashboardReducer", () => {
  it("tracks layout, discard, saving, conflict, error, reload and save transitions", () => {
    const initial = initialDashboardState(source(), layout());
    const changed = dashboardReducer(initial, { type: "layout", layout: layout(2) });
    expect(changed.dirty).toBe(true);
    expect(changed.currentLayout.widgets[0].x).toBe(2);
    expect(dashboardReducer(changed, { type: "saving" }).saveState).toBe("saving");

    const conflict = dashboardReducer(changed, {
      type: "conflict",
      message: "stale",
      currentRevision: 2,
    });
    expect(conflict.saveState).toBe("conflict");
    expect(conflict.currentLayout.widgets[0].x).toBe(2);
    expect(conflict.currentRevision).toBe(2);

    const failed = dashboardReducer(changed, { type: "error", message: "failed", status: 503 });
    expect(failed.saveState).toBe("error");
    expect(failed.dirty).toBe(true);
    expect(failed.currentLayout.widgets[0].x).toBe(2);

    const discarded = dashboardReducer(changed, { type: "discard" });
    expect(discarded.dirty).toBe(false);
    expect(discarded.currentLayout.widgets[0].x).toBe(0);

    const reloaded = dashboardReducer(changed, {
      type: "reload",
      dashboard: dashboard(3, layout(4)),
      layout: layout(4),
    });
    expect(reloaded.dirty).toBe(false);
    expect(reloaded.currentLayout.widgets[0].x).toBe(4);
    expect(reloaded.source.kind === "saved" && reloaded.source.dashboard.revision).toBe(3);
  });

  it("compares structurally, not by reference or object-key insertion order", () => {
    const left = layout(0, { source: "one", rows: 5 });
    const right = layout(0, { rows: 5, source: "one" });
    expect(left).not.toBe(right);
    expect(layoutsEqual(left, right)).toBe(true);
    const state = dashboardReducer(initialDashboardState(source(), left), {
      type: "layout",
      layout: right,
    });
    expect(state.dirty).toBe(false);
  });

  it("save success promotes the sent layout and keeps the new revision", () => {
    const changed = dashboardReducer(initialDashboardState(source(), layout()), {
      type: "layout",
      layout: layout(2),
    });
    const saved = dashboardReducer(changed, {
      type: "saved",
      dashboard: dashboard(2, layout(2)),
      layout: layout(2),
    });
    expect(saved.dirty).toBe(false);
    expect(saved.saveState).toBe("saved");
    expect(saved.message).toBe("Saved");
    expect(saved.source.kind === "saved" && saved.source.dashboard.revision).toBe(2);
  });
});

describe("useDashboard writes", () => {
  it("sends expected_revision and keeps the revision returned by a save", async () => {
    api.replaceDashboard.mockResolvedValue({ kind: "saved", dashboard: dashboard(2, layout(3)) });
    const { result } = renderHook(() => useDashboard(source(), layout(), options));
    act(() => result.current.changeLayout(layout(3)));
    await act(async () => expect((await result.current.save()).kind).toBe("saved"));
    expect(api.replaceDashboard).toHaveBeenCalledWith("d1", {
      name: "Night shift",
      description: "For the night team.",
      layout: layout(3),
      expected_revision: 1,
    });
    expect(result.current.state.source.kind === "saved" && result.current.state.source.dashboard.revision).toBe(2);
    expect(result.current.state.dirty).toBe(false);
  });

  it("a conflict keeps the local layout, and Reload replaces it with the server copy", async () => {
    api.replaceDashboard.mockResolvedValue({
      kind: "conflict",
      message: "revision changed",
      current_revision: 2,
    });
    api.getDashboard.mockResolvedValue(dashboard(2, layout(5)));
    const { result } = renderHook(() => useDashboard(source(), layout(), options));
    act(() => result.current.changeLayout(layout(3)));
    await act(async () => void (await result.current.save()));
    expect(result.current.state.saveState).toBe("conflict");
    expect(result.current.state.currentLayout.widgets[0].x).toBe(3);
    await act(async () => expect(await result.current.reload()).toBe(true));
    expect(result.current.state.currentLayout.widgets[0].x).toBe(5);
    expect(result.current.state.dirty).toBe(false);
  });

  it("Save as a copy posts the current local layout and switches to the copy", async () => {
    api.createDashboard.mockResolvedValue({ ...dashboard(1, layout(4)), id: "copy" });
    const { result } = renderHook(() => useDashboard(source(), layout(), options));
    act(() => result.current.changeLayout(layout(4)));
    await act(async () => void (await result.current.saveAsCopy("Copy of Night shift")));
    expect(api.createDashboard).toHaveBeenCalledWith({
      name: "Copy of Night shift",
      description: "For the night team.",
      layout: layout(4),
      template_key: "soc_overview",
      template_version: 1,
    });
    expect(result.current.state.source.kind === "saved" && result.current.state.source.dashboard.id).toBe("copy");
  });

  it("the template's first save creates it and sets the default when none exists", async () => {
    api.createDashboard.mockResolvedValue(dashboard());
    api.setDefaultDashboard.mockResolvedValue({ dashboard_id: "d1" });
    const { result } = renderHook(() =>
      useDashboard(
        { kind: "template", template: SOC_OVERVIEW },
        SOC_OVERVIEW.layout,
        { setDefaultOnCreate: true },
      ),
    );
    await act(async () => void (await result.current.save()));
    expect(api.createDashboard).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "SOC Overview",
        layout: SOC_OVERVIEW.layout,
        template_key: "soc_overview",
        template_version: 1,
      }),
    );
    expect(api.setDefaultDashboard).toHaveBeenCalledWith("d1");
  });

  it("a refused default after the create is not reported as a failed save", async () => {
    api.createDashboard.mockResolvedValue(dashboard());
    api.setDefaultDashboard.mockRejectedValue(new Error("refused"));
    const { result } = renderHook(() =>
      useDashboard({ kind: "template", template: SOC_OVERVIEW }, SOC_OVERVIEW.layout, {
        setDefaultOnCreate: true,
      }),
    );
    let outcome: { kind: string } | undefined;
    await act(async () => void (outcome = await result.current.save()));
    expect(outcome?.kind).toBe("saved");
    expect(result.current.state.saveState).toBe("saved");
  });

  it("registers beforeunload only while structurally dirty", () => {
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    const { result, unmount } = renderHook(() => useDashboard(source(), layout(), options));
    expect(add.mock.calls.filter(([name]) => name === "beforeunload")).toHaveLength(0);
    act(() => result.current.changeLayout(layout(1)));
    expect(add.mock.calls.filter(([name]) => name === "beforeunload")).toHaveLength(1);
    act(() => result.current.discard());
    expect(remove.mock.calls.filter(([name]) => name === "beforeunload")).toHaveLength(1);
    unmount();
  });
});
