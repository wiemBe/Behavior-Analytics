import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EditGrid } from "./Grid";
import type { LayoutWidget } from "./types";

vi.mock("react-grid-layout", () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  useContainerWidth: () => ({ width: 1000, containerRef: vi.fn(), mounted: true }),
  verticalCompactor: {},
}));

vi.mock("./registry", () => ({
  widgetDef: () => ({
    title: "Traffic",
    status: "live",
    size: { w: 4, h: 4, minW: 2, minH: 2 },
    config: {},
  }),
}));

const widget: LayoutWidget = {
  id: "traffic",
  type: "chart.traffic",
  x: 0,
  y: 0,
  w: 4,
  h: 4,
  range: null,
  config: {},
};

beforeEach(() => {
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
  } as CSSStyleDeclaration);
});

describe("EditGrid accessibility", () => {
  it("only exposes the arrange panel relationship while the panel exists", async () => {
    render(
      <EditGrid
        widgets={[widget]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        renderWidget={(_item, arrange) => arrange}
      />,
    );

    const trigger = await screen.findByRole("button", { name: "Arrange Traffic" });
    expect(trigger).not.toHaveAttribute("aria-controls");

    fireEvent.click(trigger);
    const panelId = trigger.getAttribute("aria-controls");
    expect(panelId).not.toBeNull();
    expect(document.getElementById(panelId!)).toBeInTheDocument();
  });
});
