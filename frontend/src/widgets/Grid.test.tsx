import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EditGrid, type EditGridProps } from "./Grid";
import type { LayoutWidget } from "./types";

const mocks = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("react-grid-layout", () => ({
  default: (props: Record<string, unknown> & { children: React.ReactNode }) => {
    mocks.props = props;
    return <div data-testid="grid-layout">{props.children}</div>;
  },
  useContainerWidth: () => ({
    width: 1000,
    mounted: true,
    containerRef: { current: null },
    measureWidth: vi.fn(),
  }),
  verticalCompactor: { type: "vertical", allowOverlap: false, compact: vi.fn() },
}));

vi.mock("./registry", () => ({
  widgetDef: () => ({
    title: "Total EPS",
    size: { w: 3, h: 7, minW: 2, minH: 6, maxW: 6, maxH: 9 },
  }),
}));

const widget = (
  extra: Partial<LayoutWidget> = {},
): LayoutWidget => ({
  id: "w_kpieps",
  type: "kpi.total_eps",
  x: 0,
  y: 0,
  w: 3,
  h: 7,
  range: null,
  config: {},
  ...extra,
});

beforeEach(() => {
  mocks.props = null;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
  } as CSSStyleDeclaration);
});

afterEach(() => vi.restoreAllMocks());

function view(item = widget(), onChange = vi.fn(), onRemove = vi.fn()) {
  const props: EditGridProps = {
    widgets: [item],
    onChange,
    onRemove,
    renderWidget: (_placed, arrange) => <section>{arrange}</section>,
  };
  return { ...render(<EditGrid {...props} />), onChange, onRemove };
}

function openArrange() {
  fireEvent.click(screen.getByRole("button", { name: "Arrange Total EPS" }));
}

describe("EditGrid", () => {
  it("uses the token geometry, registry bounds, title handle and south-east resize only", () => {
    view();
    expect(screen.getByTestId("grid-layout")).toBeInTheDocument();
    expect(mocks.props?.gridConfig).toEqual({
      cols: 12,
      rowHeight: 24,
      margin: [12, 12],
      containerPadding: [0, 0],
    });
    expect(mocks.props?.dragConfig).toMatchObject({
      enabled: true,
      handle: ".dg-drag-handle",
    });
    expect(mocks.props?.resizeConfig).toEqual({ enabled: true, handles: ["se"] });
    expect(mocks.props?.layout).toEqual([
      expect.objectContaining({ i: "w_kpieps", minW: 2, minH: 6, maxW: 6, maxH: 9 }),
    ]);
  });

  it("renders every named Move and Resize stepper", () => {
    view(widget({ x: 2, y: 2, w: 4, h: 7 }));
    openArrange();
    for (const name of [
      "Move Total EPS left",
      "Move Total EPS right",
      "Move Total EPS up",
      "Move Total EPS down",
      "Resize Total EPS wider",
      "Resize Total EPS narrower",
      "Resize Total EPS taller",
      "Resize Total EPS shorter",
    ]) {
      expect(screen.getByRole("button", { name })).toBeEnabled();
    }
  });

  it("disables movement at the left, top and right bounds", () => {
    const { rerender } = view(widget({ x: 0, y: 0 }));
    openArrange();
    expect(screen.getByRole("button", { name: "Move Total EPS left" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Total EPS up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Total EPS right" })).toBeEnabled();

    rerender(
      <EditGrid
        widgets={[widget({ x: 9 })]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        renderWidget={(_placed, arrange) => <section>{arrange}</section>}
      />,
    );
    expect(screen.getByRole("button", { name: "Move Total EPS right" })).toBeDisabled();
  });

  it("disables resize steppers at every registry bound and at the column edge", () => {
    const { rerender } = view(widget({ x: 6, w: 6, h: 9 }));
    openArrange();
    expect(screen.getByRole("button", { name: "Resize Total EPS wider" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resize Total EPS taller" })).toBeDisabled();

    rerender(
      <EditGrid
        widgets={[widget({ w: 2, h: 6 })]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        renderWidget={(_placed, arrange) => <section>{arrange}</section>}
      />,
    );
    expect(screen.getByRole("button", { name: "Resize Total EPS narrower" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resize Total EPS shorter" })).toBeDisabled();
  });

  it("applies keyboard-reachable movement and resize steppers through the pure helpers", () => {
    const onChange = vi.fn();
    view(widget({ x: 2, y: 2, w: 4, h: 7 }), onChange);
    openArrange();
    fireEvent.click(screen.getByRole("button", { name: "Move Total EPS right" }));
    expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: "w_kpieps", x: 3, y: 2, w: 4, h: 7 }),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Resize Total EPS taller" }));
    expect(onChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ id: "w_kpieps", x: 2, y: 2, w: 4, h: 8 }),
    ]);
  });

  it("exposes current geometry as data attributes", () => {
    const { container } = view(widget({ x: 2, y: 3, w: 4, h: 8 }));
    expect(container.querySelector(".dg-item")).toMatchObject({
      dataset: { x: "2", y: "3", w: "4", h: "8" },
    });
  });

  it("keeps Remove inside the one named arrangement disclosure", () => {
    const { onRemove } = view();
    expect(screen.getByRole("button", { name: "Arrange Total EPS" })).toHaveTextContent("Edit");
    fireEvent.click(screen.getByRole("button", { name: "Arrange Total EPS" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Total EPS" }));
    expect(onRemove).toHaveBeenCalledWith("w_kpieps");
  });
});
