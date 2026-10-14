import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";

import { DashboardView } from "./DashboardView";
import type { AnyWidgetDef, DashboardLayout } from "./types";

const mocks = vi.hoisted(() => ({
  me: { status: "loading" } as unknown,
}));

vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => mocks.me as ResourceState<unknown>,
}));

vi.mock("./registry", async () => {
  const { WidgetFrame } = await import("@/components/home/WidgetFrame");
  const def = (
    type: string,
    title: string,
    category: string,
    extra: Partial<AnyWidgetDef> = {},
  ): AnyWidgetDef =>
    ({
      type,
      title,
      description: `${title} description`,
      category,
      requires: ["read:fleet"],
      sources: ["/fleet/summary"],
      status: "live",
      size: { w: 4, h: 6, minW: 2, minH: 3 },
      range: null,
      config: {},
      defaultConfig: {},
      drillDown: () => "/fleet",
      Component: () => (
        <WidgetFrame
          id={`t-${type}`}
          title={title}
          link={{ href: "/fleet", label: "Open" }}
          what="x"
          permission="read:fleet"
          state={{ kind: "ok", data: title, asOf: null }}
        >
          {({ data }) => <p>{data}</p>}
        </WidgetFrame>
      ),
      ...extra,
    }) as AnyWidgetDef;
  const WIDGETS = [
    def("a.one", "Alpha", "KPI"),
    def("b.two", "Bravo", "Chart"),
    def("c.admin", "Charlie", "Admin", { requires: ["admin:outage"] }),
    def("d.blocked", "Delta", "Table", {
      status: "blocked",
      blockedBy: "its data is not collected yet",
      requires: [],
      sources: [],
    }),
  ];
  return { WIDGETS, widgetDef: (t: string) => WIDGETS.find((w) => w.type === t) };
});

const layout: DashboardLayout = {
  v: 1,
  range: "24h",
  widgets: [
    { id: "w_bravo", type: "b.two", x: 4, y: 0, w: 4, h: 6, range: null, config: {} },
    { id: "w_alpha", type: "a.one", x: 0, y: 0, w: 4, h: 6, range: null, config: {} },
  ],
};

let width = 1000;
let observed: Array<(entries: { contentRect: { width: number } }[]) => void> = [];

beforeEach(() => {
  mocks.me = { status: "loading" };
  width = 1000;
  observed = [];
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ width, height: 100, top: 0, left: 0, right: width, bottom: 100, x: 0, y: 0 }) as DOMRect,
  );
  globalThis.ResizeObserver = class {
    constructor(cb: (entries: { contentRect: { width: number } }[]) => void) {
      observed.push(cb);
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
  } as CSSStyleDeclaration);
  // jsdom has no modal dialogs.
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

afterEach(() => vi.restoreAllMocks());

const view = (l: DashboardLayout = layout) =>
  render(<DashboardView layout={l} renderHeader={(actions) => <div>{actions}</div>} />);

const resize = (w: number) => {
  width = w;
  act(() => observed.forEach((callback) => callback([{ contentRect: { width: w } }])));
};

describe("DashboardView", () => {
  it("renders the layout's widgets through the registry in reading order", () => {
    view();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Alpha",
      "Bravo",
    ]);
  });

  it("renders an unknown type as an Unsupported widget and keeps the others", () => {
    view({
      ...layout,
      widgets: [
        ...layout.widgets,
        { id: "w_zzzz", type: "future.widget", x: 8, y: 0, w: 4, h: 6, range: null, config: {} },
      ],
    });
    expect(screen.getByRole("heading", { name: "Unsupported widget" })).toBeInTheDocument();
    expect(screen.getByText(/type future\.widget that this version does not know/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Alpha" })).toBeInTheDocument();
  });

  it("renders a blocked type with its reason and no data", () => {
    view({
      ...layout,
      widgets: [
        { id: "w_dddd", type: "d.blocked", x: 0, y: 0, w: 4, h: 6, range: null, config: {} },
      ],
    });
    expect(screen.getByRole("heading", { name: "Delta" })).toBeInTheDocument();
    expect(screen.getByText("Not available: its data is not collected yet.")).toBeInTheDocument();
  });

  it("derives 6 columns from the container width and stacks one column when narrow", () => {
    const { container } = view();
    const grid = container.querySelector(".dg")!;
    expect(grid).toHaveAttribute("data-cols", "12");
    resize(700);
    expect(grid).toHaveAttribute("data-cols", "6");
    resize(500);
    expect(grid).toHaveAttribute("data-cols", "1");
  });
});

describe("customize mode", () => {
  async function openArrange(title: string) {
    const arrange = await screen.findByRole("button", { name: `Arrange ${title}` });
    fireEvent.click(arrange);
  }

  it("is off until asked: no Remove control, no session line", () => {
    view();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.queryByText(/session only/)).toBeNull();
    expect(screen.getAllByRole("link", { name: "Open" })).toHaveLength(2);
  });

  it("says plainly that changes last for this session only", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    expect(screen.getByText(/Customizing\. Changes last/)).toHaveTextContent(
      "Customizing. Changes last for this session only; saving is not built yet.",
    );
  });

  it("removes a widget with a control named for it, and Done leaves the mode", async () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await openArrange("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "Remove Alpha" }));
    expect(screen.queryByRole("heading", { name: "Alpha" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Bravo" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Customize" })).toBeInTheDocument();
    // What was removed stays removed for the session.
    expect(screen.queryByRole("heading", { name: "Alpha" })).toBeNull();
  });

  it("adds a widget at the first free spot at its default size", async () => {
    const { container } = view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Arrange Alpha" });
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Charlie" }));
    // The layout holds x 0-8 on row 0 of 4 + 4; a 4-wide widget fits at x 8.
    const added = container.querySelector<HTMLElement>('.dg-item[data-type="c.admin"]')!;
    expect(added).toHaveAttribute("data-x", "8");
    expect(added).toHaveAttribute("data-y", "0");
    expect(added).toHaveAttribute("data-w", "4");
    expect(added).toHaveAttribute("data-h", "6");
    expect(screen.getByRole("status")).toHaveTextContent("Added Charlie.");
    // Adding closes the catalog.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("fills the gap left by a removal when the widget is added back", async () => {
    const { container } = view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await openArrange("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "Remove Alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    fireEvent.click(screen.getByRole("button", { name: "Add Alpha" }));
    const back = container.querySelector<HTMLElement>('.dg-item[data-type="a.one"]')!;
    expect(back).toHaveAttribute("data-x", "0");
    expect(back).toHaveAttribute("data-y", "0");
  });

  it("does not mount the edit grid in read mode or below 12 columns", async () => {
    const { container } = view();
    expect(container.querySelector(".dg-edit-shell")).toBeNull();
    resize(700);
    expect(screen.getByText("Widen the window to customize")).toBeInTheDocument();
    expect(container.querySelector(".dg-edit-shell")).toBeNull();
    resize(1000);
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Arrange Alpha" });
    expect(container.querySelector(".dg-edit-shell")).toBeInTheDocument();
    resize(700);
    expect(container.querySelector(".dg-edit-shell")).toBeNull();
  });

  it("Done keeps keyboard layout changes in memory", async () => {
    const { container } = view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await openArrange("Alpha");
    fireEvent.click(screen.getByRole("button", { name: "Move Alpha right" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    const alpha = container.querySelector<HTMLElement>('.dg-item[data-type="a.one"]')!;
    expect(alpha).toHaveAttribute("data-x", "1");
  });

  it("offers no customize below 12 columns, and says why", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    resize(700);
    expect(screen.queryByRole("button", { name: "Customize" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByText("Widen the window to customize")).toBeInTheDocument();
    resize(1000);
    expect(screen.getByRole("button", { name: "Done" })).toBeInTheDocument();
  });
});

describe("catalog drawer", () => {
  function open() {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    const trigger = screen.getByRole("button", { name: "Add widget" });
    trigger.focus();
    fireEvent.click(trigger);
    return { trigger, dialog: screen.getByRole("dialog", { name: "Add a widget" }) };
  }

  it("lists widgets by category with what each reads and needs", () => {
    const { dialog } = open();
    const categories = within(dialog)
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent);
    expect(categories).toEqual(["KPI", "Chart", "Table", "Admin"]);
    expect(within(dialog).getByText("Alpha description")).toBeInTheDocument();
    expect(within(dialog).getAllByText("/fleet/summary").length).toBeGreaterThan(0);
    expect(within(dialog).getByRole("button", { name: "Add Charlie" })).toBeEnabled();
  });

  it("disables a widget already on the dashboard and says so", () => {
    const { dialog } = open();
    expect(within(dialog).getByRole("button", { name: "Add Alpha" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Add Bravo" })).toBeDisabled();
    expect(within(dialog).getAllByText("Already on this dashboard.")).toHaveLength(2);
    expect(within(dialog).getByRole("button", { name: "Add Charlie" })).toBeEnabled();
  });

  it("shows 'Already on this dashboard' as neutral information, and a blocked reason as a warning", () => {
    const { dialog } = open();
    for (const el of within(dialog).getAllByText("Already on this dashboard.")) {
      expect(el).toHaveClass("dg-catalog-reason", "info");
    }
    const blocked = within(dialog).getByText("Not available: its data is not collected yet.");
    expect(blocked).toHaveClass("dg-catalog-reason");
    expect(blocked).not.toHaveClass("info");
  });

  it("disables a blocked widget and gives the reason", () => {
    const { dialog } = open();
    expect(within(dialog).getByRole("button", { name: "Add Delta" })).toBeDisabled();
    expect(within(dialog).getByText("Not available: its data is not collected yet.")).toBeInTheDocument();
  });

  it("disables a widget the role cannot read, naming the permission, once /me answers", () => {
    mocks.me = {
      status: "ok",
      data: { subject: "u", email: null, permissions: [], effective_permissions: ["read:fleet"] },
      receivedAt: "2026-10-14T12:00:00Z",
      refreshing: false,
      refreshFailed: null,
    };
    const { dialog } = open();
    expect(within(dialog).getByRole("button", { name: "Add Charlie" })).toBeDisabled();
    expect(
      within(dialog).getByText("Your role lacks permission admin:outage."),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Add Alpha" })).toBeDisabled();
  });

  it("lists everything when /me cannot be read, and says so", () => {
    mocks.me = { status: "error", httpStatus: 500 };
    const { dialog } = open();
    expect(within(dialog).getByRole("button", { name: "Add Charlie" })).toBeEnabled();
    expect(within(dialog).getByText(/permissions could not be read/)).toBeInTheDocument();
  });

  it("moves focus in on open and returns it to the trigger on Escape", () => {
    const { trigger, dialog } = open();
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Close" }));
    const cancel = new Event("cancel", { cancelable: true });
    act(() => {
      dialog.dispatchEvent(cancel);
    });
    expect(cancel.defaultPrevented).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("returns focus to the trigger on Close", () => {
    const { trigger, dialog } = open();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(document.activeElement).toBe(trigger);
  });
});
