// The configurable widgets inside a dashboard (UI-3.5b): a second copy of a widget that has
// config keys can be added, one without cannot; a widget whose subject is not chosen offers
// Configure while customizing, and the drawer's Apply gives the widget its id.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardView } from "./DashboardView";
import type { AnyWidgetDef, DashboardLayout } from "./types";

vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) =>
    key === "/behavior/sources"
      ? {
          status: "ok",
          receivedAt: "2026-10-14T13:58:00Z",
          refreshing: false,
          refreshFailed: null,
          data: [
            { log_source_id: "src-a", name: "fw-edge-01" },
            { log_source_id: "src-b", name: "dc-win-02" },
          ],
        }
      : { status: "loading" },
}));

vi.mock("./registry", async () => {
  const { WidgetFrame } = await import("@/components/home/WidgetFrame");
  const { ConfigNotice } = await import("@/components/catalog/ConfigNotice");
  const { MONITORED_SOURCES } = await import("./optionSources");
  const base = {
    category: "Chart",
    requires: ["read:fleet"],
    sources: ["/fleet/summary"],
    status: "live",
    size: { w: 4, h: 6, minW: 2, minH: 3 },
    range: null,
    expandable: false,
    drillDown: () => null,
  };
  const subject = {
    ...base,
    type: "t.subject",
    title: "Subject chart",
    description: "Needs a source.",
    config: { source: { label: "Log source", from: MONITORED_SOURCES } },
    defaultConfig: { source: null },
    Component: ({ config }: { config: { source: string | null } }) => (
      <WidgetFrame
        id="t-subject"
        title="Subject chart"
        link={{ href: "/x", label: "Open" }}
        what="x"
        permission="read:fleet"
        state={{ kind: "ok", data: config.source, asOf: null }}
      >
        {({ data }) =>
          data === null ? (
            <ConfigNotice lead="Choose a log source." text="Nothing set." hint="Use Configure." />
          ) : (
            <p>Drawing {data}</p>
          )
        }
      </WidgetFrame>
    ),
  };
  const plain = {
    ...base,
    type: "t.plain",
    title: "Plain table",
    description: "No config.",
    config: {},
    defaultConfig: {},
    Component: () => null,
  };
  const WIDGETS = [subject, plain] as unknown as AnyWidgetDef[];
  return { WIDGETS, widgetDef: (t: string) => WIDGETS.find((w) => w.type === t) };
});

const layout: DashboardLayout = {
  v: 1,
  range: "24h",
  widgets: [{ id: "w_subj1", type: "t.subject", x: 0, y: 0, w: 4, h: 6, range: null, config: { source: null } }],
};

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ width: 1000, height: 100, top: 0, left: 0, right: 1000, bottom: 100, x: 0, y: 0 }) as DOMRect,
  );
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
  } as CSSStyleDeclaration);
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

afterEach(() => vi.restoreAllMocks());

const view = () =>
  render(<DashboardView layout={layout} renderHeader={(actions) => <div>{actions}</div>} />);

describe("duplicates", () => {
  it("lets a widget with config keys be added twice, and refuses a second one without", async () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Arrange Subject chart" });
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    // The subject widget is already placed, and can still be added.
    expect(screen.getByRole("button", { name: "Add Subject chart" })).toBeEnabled();
    expect(screen.queryByText("Already on this dashboard.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add Subject chart" }));

    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Plain table" }));
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    expect(screen.getByRole("button", { name: "Add Plain table" })).toBeDisabled();
    expect(screen.getByText("Already on this dashboard.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add Subject chart" })).toBeEnabled();
  });

  it("starts a newly added configurable widget with no subject", async () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Arrange Subject chart" });
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Subject chart" }));
    expect(screen.getAllByText("Choose a log source.")).toHaveLength(2);
  });
});

describe("a widget with no subject", () => {
  it("says how to choose one in read mode, with no Configure action", () => {
    view();
    expect(screen.getByRole("status")).toHaveTextContent("Choose a log source. Nothing set. Use Configure.");
    expect(screen.queryByRole("button", { name: "Configure" })).toBeNull();
  });

  it("offers Configure while customizing; Apply gives the widget the chosen id", async () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Arrange Subject chart" });
    const action = screen.getByRole("button", { name: "Configure" });
    fireEvent.click(action);
    const drawer = await screen.findByRole("dialog", { name: "Configure Subject chart" });
    const apply = within(drawer).getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    fireEvent.change(within(drawer).getByRole("combobox", { name: "Log source" }), {
      target: { value: "src-b" },
    });
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(screen.getByText("Drawing src-b")).toBeInTheDocument());
    expect(screen.queryByText("Choose a log source.")).toBeNull();
  });
});
