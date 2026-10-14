import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigForm, ConfigureDrawer, hasConfigure } from "./Configure";
import type { AnyWidgetDef, LayoutWidget, WidgetDef } from "./types";

// A test-only widget: no registry entry has config yet (UI-3.5b adds the first).
interface TestConfig {
  metric: "eps" | "events";
  top: 5 | 10 | 25;
}

const testDef: WidgetDef<TestConfig> = {
  type: "test.configurable",
  title: "Test widget",
  description: "Only for tests.",
  category: "Table",
  requires: ["read:fleet"],
  sources: ["/fleet/summary"],
  status: "live",
  size: { w: 4, h: 6, minW: 2, minH: 3 },
  range: ["1h", "24h", "7d"],
  expandable: true,
  config: {
    metric: { label: "Measure", options: ["eps", "events"] },
    top: { label: "Rows", options: [5, 10, 25] },
  },
  defaultConfig: { metric: "eps", top: 5 },
  drillDown: () => null,
  Component: () => null,
};
const def = testDef as AnyWidgetDef;

const placed = (over: Partial<LayoutWidget> = {}): LayoutWidget => ({
  id: "w_test1",
  type: def.type,
  x: 0,
  y: 0,
  w: 4,
  h: 6,
  range: null,
  config: { metric: "eps", top: 5 },
  ...over,
});

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

describe("hasConfigure", () => {
  it("is true for a range, or a config key, and false for neither", () => {
    expect(hasConfigure(def)).toBe(true);
    expect(hasConfigure({ ...def, range: null } as AnyWidgetDef)).toBe(true);
    expect(hasConfigure({ ...def, config: {} } as AnyWidgetDef)).toBe(true);
    expect(hasConfigure({ ...def, range: null, config: {} } as AnyWidgetDef)).toBe(false);
  });
});

describe("ConfigForm", () => {
  it("renders one labelled select per key with exactly the schema's options, and no text input", () => {
    const { container } = render(
      <ConfigForm schema={def.config} values={{ metric: "eps", top: 10 }} onChange={vi.fn()} />,
    );
    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(2);
    expect(
      within(screen.getByRole("combobox", { name: "Measure" }))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["eps", "events"]);
    expect(
      within(screen.getByRole("combobox", { name: "Rows" }))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["5", "10", "25"]);
    expect(screen.getByRole("combobox", { name: "Rows" })).toHaveDisplayValue("10");
    expect(container.querySelector("input, textarea")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("reports the option's own value, a number as a number", () => {
    const onChange = vi.fn();
    render(<ConfigForm schema={def.config} values={{ metric: "eps", top: 5 }} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Rows" }), { target: { value: "2" } });
    expect(onChange).toHaveBeenCalledWith("top", 25);
  });
});

describe("ConfigureDrawer", () => {
  const drawer = (widget = placed(), onApply = vi.fn(), onClose = vi.fn()) => {
    render(
      <ConfigureDrawer
        def={def}
        widget={widget}
        dashboardRange="7d"
        onApply={onApply}
        onClose={onClose}
      />,
    );
    return { onApply, onClose };
  };

  it("offers the presets and 'Follow the dashboard' showing the dashboard's actual range", () => {
    drawer();
    const range = screen.getByRole("combobox", { name: "Time range" });
    expect(within(range).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Follow the dashboard (7 d)",
      "1 h",
      "24 h",
      "7 d",
    ]);
    expect(range).toHaveDisplayValue("Follow the dashboard (7 d)");
    expect(screen.getByRole("dialog", { name: "Configure Test widget" })).toBeInTheDocument();
  });

  it("applies the chosen range and config", () => {
    const { onApply, onClose } = drawer();
    fireEvent.change(screen.getByRole("combobox", { name: "Time range" }), {
      target: { value: "1h" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Measure" }), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ range: "1h", config: { metric: "events", top: 5 } });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("applies null when the widget is set back to follow the dashboard", () => {
    const { onApply } = drawer(placed({ range: "24h" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Time range" }), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ range: null, config: { metric: "eps", top: 5 } });
  });

  it("Cancel and Esc apply nothing", () => {
    const { onApply, onClose } = drawer();
    fireEvent.change(screen.getByRole("combobox", { name: "Time range" }), {
      target: { value: "1h" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    const cancel = new Event("cancel", { cancelable: true });
    screen.getByRole("dialog").dispatchEvent(cancel);
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(cancel.defaultPrevented).toBe(true);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("shows no range select for a widget whose reads take no window", () => {
    render(
      <ConfigureDrawer
        def={{ ...def, range: null } as AnyWidgetDef}
        widget={placed()}
        dashboardRange="24h"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.queryByRole("combobox", { name: "Time range" })).toBeNull();
    expect(screen.getAllByRole("combobox")).toHaveLength(2);
  });
});
