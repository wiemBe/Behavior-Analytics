// Per-widget range, the configure drawer and the expand dialog, through the dashboard (UI-3.3).

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";

import { DashboardView } from "./DashboardView";
import type { AnyWidgetDef, DashboardLayout } from "./types";

vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => ({ status: "loading" }) as ResourceState<unknown>,
}));

vi.mock("./registry", async () => {
  const { WidgetFrame } = await import("@/components/home/WidgetFrame");
  const { useEffectiveRange } = await import("./runtime");
  // A probe widget: it shows the props and the range it reads.
  const probe = (type: string, title: string) => {
    function Probe({
      range,
      expanded,
      config,
    }: {
      range: string | null;
      expanded: boolean;
      config: unknown;
    }) {
      const effective = useEffectiveRange(range as never);
      return (
        <WidgetFrame
          id={`t-${type}`}
          title={title}
          windowText={effective}
          link={{ href: "/fleet", label: "Open" }}
          what="x"
          permission="read:fleet"
          state={{ kind: "ok", data: title, asOf: null }}
        >
          {() => (
            <p data-testid={`probe-${type}`}>
              {`reads ${effective}; expanded ${expanded}; config ${JSON.stringify(config)}`}
            </p>
          )}
        </WidgetFrame>
      );
    }
    return Probe;
  };
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
      expandable: false,
      config: {},
      defaultConfig: {},
      drillDown: () => "/fleet",
      Component: probe(type, title),
      ...extra,
    }) as AnyWidgetDef;
  const WIDGETS = [
    // Ranged and expandable: a chart.
    def("r.chart", "Ranged chart", "Chart", { range: ["1h", "24h", "7d"], expandable: true }),
    // Neither range nor config, not expandable: a card.
    def("p.plain", "Plain card", "Health"),
    // Config but no range, expandable: a table with a test-only schema.
    def("c.table", "Config table", "Table", {
      expandable: true,
      config: { rows: { label: "Rows", options: [5, 10] } },
      defaultConfig: { rows: 5 },
    }),
  ];
  return { WIDGETS, widgetDef: (t: string) => WIDGETS.find((w) => w.type === t) };
});

const layout = (dashboard: DashboardLayout["range"] = "24h"): DashboardLayout => ({
  v: 1,
  range: dashboard,
  widgets: [
    { id: "w_chart", type: "r.chart", x: 0, y: 0, w: 4, h: 6, range: null, config: {} },
    { id: "w_plain", type: "p.plain", x: 4, y: 0, w: 4, h: 6, range: null, config: {} },
    { id: "w_table", type: "c.table", x: 8, y: 0, w: 4, h: 6, range: null, config: { rows: 5 } },
  ],
});

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ width: 1000, height: 100, top: 0, left: 0, right: 1000, bottom: 100, x: 0, y: 0 }) as DOMRect,
  );
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

const view = (l = layout()) =>
  render(<DashboardView layout={l} renderHeader={(actions) => <div>{actions}</div>} />);

const probe = (type: string) => screen.getByTestId(`probe-${type}`);

describe("per-widget range in read mode", () => {
  it("follows the dashboard's range until the widget has its own", () => {
    view(layout("7d"));
    expect(probe("r.chart")).toHaveTextContent("reads 7d");
    fireEvent.click(
      within(screen.getByRole("group", { name: "Window" })).getByRole("button", { name: "1 h" }),
    );
    expect(probe("r.chart")).toHaveTextContent("reads 1h");
    expect(screen.getByRole("heading", { name: /Ranged chart/ })).toHaveTextContent("1h");
  });

  it("shows the control only on a widget that declares a range", () => {
    view();
    expect(screen.getAllByRole("group", { name: "Window" })).toHaveLength(1);
    const plain = screen.getByRole("region", { name: /^Plain card/ });
    expect(within(plain).queryByRole("group")).toBeNull();
    expect(within(plain).queryByRole("combobox")).toBeNull();
  });

  it("passes the config and expanded: false to the component", () => {
    view();
    expect(probe("c.table")).toHaveTextContent('expanded false; config {"rows":5}');
  });
});

describe("expand dialog", () => {
  it("is offered on expandable widgets only", () => {
    view();
    expect(screen.getByRole("button", { name: "Expand Ranged chart" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand Config table" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Expand Plain card" })).toBeNull();
  });

  it("opens with the same component, config and range, expanded", () => {
    view(layout("7d"));
    fireEvent.click(screen.getByRole("button", { name: "Expand Ranged chart" }));
    const dialog = screen.getByRole("dialog", { name: /Ranged chart/ });
    expect(dialog).toHaveAttribute("open");
    expect(within(dialog).getByRole("heading", { level: 2 })).toHaveTextContent("Ranged chart7 d");
    expect(within(dialog).getByTestId("probe-r.chart")).toHaveTextContent("reads 7d; expanded true");
    // The dialog's range control re-reads the widget.
    fireEvent.click(within(dialog).getByRole("button", { name: "1 h" }));
    expect(within(dialog).getByTestId("probe-r.chart")).toHaveTextContent("reads 1h");
  });

  it("carries the widget's config to the expanded copy", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Expand Config table" }));
    expect(within(screen.getByRole("dialog")).getByTestId("probe-c.table")).toHaveTextContent(
      'expanded true; config {"rows":5}',
    );
  });

  it("closes on Esc and on Close, and returns focus to the Expand control", async () => {
    view();
    const trigger = screen.getByRole("button", { name: "Expand Ranged chart" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    await act(async () => {
      dialog.dispatchEvent(new Event("cancel", { cancelable: true }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();

    trigger.blur();
    fireEvent.click(trigger);
    await act(async () => {
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(trigger).toHaveFocus();
  });
});

describe("customize mode", () => {
  const customize = async () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Customize" }));
    await screen.findByRole("button", { name: "Arrange Ranged chart" });
  };

  it("keeps one control in the title row: no preset, Expand or Configure beside Edit", async () => {
    await customize();
    const section = screen.getByRole("region", { name: /^Ranged chart/ });
    const head = section.querySelector("header")!;
    expect(within(head).getAllByRole("button").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Arrange Ranged chart",
    ]);
    expect(within(head).queryByRole("group")).toBeNull();
  });

  it("puts Configure and Expand inside the Edit disclosure, on the widgets that have them", async () => {
    await customize();
    fireEvent.click(screen.getByRole("button", { name: "Arrange Ranged chart" }));
    expect(screen.getByRole("button", { name: "Configure Ranged chart" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand Ranged chart" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Arrange Plain card" }));
    expect(screen.queryByRole("button", { name: "Configure Plain card" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Expand Plain card" })).toBeNull();
  });

  it("applies a range from the drawer to the layout, and Cancel leaves it unchanged", async () => {
    await customize();
    const open = async () => {
      const trigger = screen.getByRole("button", { name: "Configure Ranged chart" });
      trigger.focus();
      fireEvent.click(trigger);
      return trigger;
    };
    fireEvent.click(screen.getByRole("button", { name: "Arrange Ranged chart" }));
    await open();
    fireEvent.change(screen.getByRole("combobox", { name: "Time range" }), {
      target: { value: "1h" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(probe("r.chart")).toHaveTextContent("reads 24h");

    const trigger = await open();
    fireEvent.change(screen.getByRole("combobox", { name: "Time range" }), {
      target: { value: "1h" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(probe("r.chart")).toHaveTextContent("reads 1h");
    expect(trigger).toHaveFocus();
  });

  it("applies a config choice to a widget with config and no range", async () => {
    await customize();
    fireEvent.click(screen.getByRole("button", { name: "Arrange Config table" }));
    fireEvent.click(screen.getByRole("button", { name: "Configure Config table" }));
    const drawer = screen.getByRole("dialog", { name: "Configure Config table" });
    expect(within(drawer).queryByRole("combobox", { name: "Time range" })).toBeNull();
    fireEvent.change(within(drawer).getByRole("combobox", { name: "Rows" }), {
      target: { value: "1" },
    });
    await act(async () => {
      fireEvent.click(within(drawer).getByRole("button", { name: "Apply" }));
    });
    expect(probe("c.table")).toHaveTextContent('config {"rows":10}');
  });

  it("expands from the Edit disclosure and returns focus to that control", async () => {
    await customize();
    fireEvent.click(screen.getByRole("button", { name: "Arrange Ranged chart" }));
    const trigger = screen.getByRole("button", { name: "Expand Ranged chart" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: /Ranged chart/ });
    expect(within(dialog).getByTestId("probe-r.chart")).toHaveTextContent("expanded true");
    await act(async () => {
      dialog.dispatchEvent(new Event("cancel", { cancelable: true }));
    });
    expect(trigger).toHaveFocus();
  });
});
