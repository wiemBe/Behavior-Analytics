// The Geography widget's one choice, the address the country is taken from: a fixed list whose
// options are worded by the catalog (Source address, Destination address), not shown as the
// stored enum values. The choice is stored as the enum.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";

import { ConfigureDrawer } from "./Configure";
import { widgetDef } from "./registry";
import type { AnyWidgetDef, LayoutWidget } from "./types";

const def = widgetDef("map.geography") as AnyWidgetDef;

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

const placed = (config: Record<string, unknown> = {}): LayoutWidget => ({
  id: "w_geo01",
  type: def.type,
  x: 0,
  y: 0,
  w: 6,
  h: 24,
  range: null,
  config,
});

function open(widget = placed({ direction: "SOURCE" })) {
  const onApply = vi.fn();
  render(
    <ConfigureDrawer
      def={def}
      widget={widget}
      dashboardRange="24h"
      onApply={onApply}
      onClose={vi.fn()}
    />,
  );
  return onApply;
}

describe("configuring the Geography widget", () => {
  it("offers the two addresses by their words, not by the stored enum", () => {
    open();
    const select = screen.getByLabelText("Address");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Source address",
      "Destination address",
    ]);
    expect(select).toHaveDisplayValue("Source address");
  });

  it("stores the enum value of the option chosen, and the range choice beside it", () => {
    const onApply = open();
    fireEvent.change(screen.getByLabelText("Address"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ range: null, config: { direction: "DESTINATION" } });
  });

  it("starts from the default direction for a widget that stored none", () => {
    open(placed({}));
    expect(screen.getByLabelText("Address")).toHaveDisplayValue("Source address");
  });

  it("words the options in Turkish", () => {
    setTestLocale("tr");
    open();
    const select = screen.getByLabelText("Adres");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Kaynak adres",
      "Hedef adres",
    ]);
  });

  it("shows a fixed option that has no catalog label as its own value", () => {
    // The WAF traffic widget has read fields only; a widget with a plain list keeps its values.
    const rows = { ...def, config: { rows: { label: "Rows", options: [5, 10] } }, defaultConfig: { rows: 5 } };
    const onApply = vi.fn();
    render(
      <ConfigureDrawer
        def={rows as AnyWidgetDef}
        widget={{ ...placed({ rows: 5 }), type: "x.unknown" }}
        dashboardRange="24h"
        onApply={onApply}
        onClose={vi.fn()}
      />,
    );
    expect(within(screen.getByLabelText("Rows")).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "5",
      "10",
    ]);
  });
});
