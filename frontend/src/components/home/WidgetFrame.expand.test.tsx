import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { WidgetEditContext, WidgetFrame } from "./WidgetFrame";

const runtime = (over: Partial<WidgetRuntime> = {}): WidgetRuntime => ({
  rangeOptions: ["1h", "24h", "7d"],
  override: null,
  dashboardRange: "24h",
  setRange: vi.fn(),
  onExpand: vi.fn(),
  expanded: false,
  ...over,
});

function frame(rt: WidgetRuntime | null, kpi = false) {
  return render(
    <WidgetRuntimeContext.Provider value={rt}>
      <WidgetFrame
        id="f"
        title="Fleet state"
        windowText="24 h"
        link={{ href: "/fleet", label: "Fleet" }}
        what="the fleet state"
        permission="read:fleet"
        state={{ kind: "ok", data: "answer", asOf: null }}
        variant={kpi ? "kpi" : "panel"}
        description="What the figures mean."
      >
        {({ data }) => <p>{data}</p>}
      </WidgetFrame>
    </WidgetRuntimeContext.Provider>,
  );
}

describe("WidgetFrame range and Expand chrome", () => {
  it("puts the presets, Expand and the drill-down link in one title row", () => {
    frame(runtime());
    const head = screen.getByRole("heading", { level: 2 }).closest("header")!;
    expect(head.querySelector(".wr-presets")).not.toBeNull();
    expect(head.querySelector(".tw-expand")).toHaveTextContent("Expand");
    expect(head.querySelector("a")).toHaveAttribute("href", "/fleet");
  });

  it("hands the Expand control itself to onExpand, so focus can return to it", () => {
    const onExpand = vi.fn();
    frame(runtime({ onExpand }));
    const button = screen.getByRole("button", { name: "Expand Fleet state" });
    fireEvent.click(button);
    expect(onExpand).toHaveBeenCalledWith(button);
  });

  it("offers no Expand when the widget is not expandable, and no presets without a range", () => {
    frame(runtime({ onExpand: null, rangeOptions: null }));
    expect(screen.queryByRole("button", { name: /^Expand/ })).toBeNull();
    expect(screen.queryByRole("group", { name: "Window" })).toBeNull();
  });

  it("keeps the title row to the Edit control in customize mode", () => {
    render(
      <WidgetRuntimeContext.Provider value={runtime()}>
        <WidgetEditContext.Provider
          value={{ onRemove: vi.fn(), arrange: <button type="button">Edit</button> }}
        >
          <WidgetFrame
            id="f"
            title="Fleet state"
            link={{ href: "/fleet", label: "Fleet" }}
            what="the fleet state"
            permission="read:fleet"
            state={{ kind: "ok", data: "answer", asOf: null }}
          >
            {({ data }) => <p>{data}</p>}
          </WidgetFrame>
        </WidgetEditContext.Provider>
      </WidgetRuntimeContext.Provider>,
    );
    const head = screen.getByRole("heading", { level: 2 }).closest("header")!;
    expect(Array.from(head.querySelectorAll("button"), (b) => b.textContent)).toEqual(["Edit"]);
    expect(head.querySelector("a")).toBeNull();
  });

  it("expanded: no title row of its own, named by aria-label, ids that do not repeat", () => {
    const { container } = frame(runtime({ expanded: true, onExpand: null }));
    expect(container.querySelector("header")).toBeNull();
    expect(screen.queryByRole("heading")).toBeNull();
    const region = screen.getByRole("region", { name: "Fleet state" });
    expect(region).toHaveAccessibleDescription("What the figures mean.");
    expect(container.querySelector("#f-expanded-desc")).not.toBeNull();
    expect(container.querySelector("#f-desc")).toBeNull();
    expect(screen.getByText("answer")).toBeInTheDocument();
  });

  it("uses a select at KPI density", () => {
    frame(runtime(), true);
    expect(screen.getByRole("combobox", { name: "Window for Fleet state" })).toHaveDisplayValue("24 h");
    expect(screen.queryByRole("group", { name: "Window" })).toBeNull();
  });
});

describe("WidgetFrame narrow panel title row", () => {
  afterEach(() => vi.restoreAllMocks());
  // The pill moves by fit (T-098), not by a width: `child` is what each child of the title row
  // measures, so a row of three children needs three times that.
  const width = (px: number, child = 0) => {
    vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(px);
    if (child > 0) vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(child);
  };
  const badged = (rt: WidgetRuntime) =>
    render(
      <WidgetRuntimeContext.Provider value={rt}>
        <WidgetFrame
          id="f"
          title="EPS by source class"
          windowText="24 h"
          badge="Partial: 21 of 24 intervals"
          link={{ href: "/fleet", label: "Fleet" }}
          what="the fleet"
          permission="read:fleet"
          state={{ kind: "ok", data: "answer", asOf: null }}
        >
          {({ data }) => <p>{data}</p>}
        </WidgetFrame>
      </WidgetRuntimeContext.Provider>,
    );

  it("moves only the pill to the first body line; range, Expand and link stay in the title row", () => {
    width(366, 200);
    badged(runtime());
    const head = screen.getByRole("heading", { level: 2 }).closest("header")!;
    const pill = screen.getByText("Partial: 21 of 24 intervals");
    expect(head.contains(pill)).toBe(false);
    expect(pill.parentElement).toHaveClass("tw-body");
    expect(pill.parentElement!.firstElementChild).toBe(pill);
    expect(head.querySelector(".tw-expand")).not.toBeNull();
    expect(head.querySelector(".wr-select")).not.toBeNull();
    expect(head.querySelector("a")).toHaveAttribute("href", "/fleet");
  });

  it("keeps the select, Expand and the drill-down link in one wrapper, so they wrap as one unit", () => {
    width(366, 200);
    badged(runtime());
    const group = screen.getByRole("link", { name: /Fleet/ }).closest(".tw-tools")!;
    expect(group.querySelector(".wr-select")).not.toBeNull();
    expect(group.querySelector(".tw-expand")).not.toBeNull();
    expect(group.parentElement).toBe(screen.getByRole("heading", { level: 2 }).closest("header"));
  });

  it("keeps the pill beside the title in a wide panel", () => {
    width(700);
    badged(runtime());
    const head = screen.getByRole("heading", { level: 2 }).closest("header")!;
    expect(head.contains(screen.getByText("Partial: 21 of 24 intervals"))).toBe(true);
  });
});
