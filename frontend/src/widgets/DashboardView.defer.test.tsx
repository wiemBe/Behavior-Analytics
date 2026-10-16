// The read grid defers off-screen widget bodies (UR.8.3): a box is drawn at once with its title
// and the loading line, its body is mounted once the box has come near the viewport, and
// Customize mode mounts everything because dragging needs the real sizes.
//
// Which boxes start deferred is decided by the layout, in the first render: the box's row against
// the grid's row pitch. The rect is deliberately far below the viewport in every test here, so a
// first-commit mount can only come from the layout. The rect mock is what drives the column count
// (the read grid measures its own width); the in-view decision does not read it.
//
// The existing DashboardView.test.tsx runs with no IntersectionObserver (jsdom has none), which
// is the "mount everything at once" path; here one is mocked, so the deferral can be driven.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardView } from "./DashboardView";
import type { AnyWidgetDef, DashboardLayout } from "./types";

vi.mock("./registry", () => {
  const def = (type: string, title: string, category: string): AnyWidgetDef =>
    ({
      type,
      title,
      description: `${title} description`,
      category,
      requires: [],
      sources: [],
      status: "live",
      size: { w: 4, h: 6, minW: 2, minH: 3 },
      range: null,
      expandable: false,
      config: {},
      defaultConfig: {},
      drillDown: () => null,
      Component: () => <p className="tw-body">{title} body</p>,
    }) as AnyWidgetDef;
  const WIDGETS = [def("a.one", "Alpha", "KPI"), def("b.two", "Bravo", "Chart")];
  return { WIDGETS, widgetDef: (t: string) => WIDGETS.find((w) => w.type === t) };
});

class MockObserver {
  static all: MockObserver[] = [];
  observed: Element[] = [];
  disconnected = false;
  constructor(
    public callback: IntersectionObserverCallback,
    public options?: IntersectionObserverInit,
  ) {
    MockObserver.all.push(this);
  }
  observe(el: Element) {
    this.observed.push(el);
  }
  unobserve() {}
  disconnect() {
    this.disconnected = true;
  }
}

/** Answers the observer that watches `el`, as the browser would when it scrolls into view. */
function intersect(el: Element) {
  const observer = MockObserver.all.find((o) => o.observed.includes(el));
  if (!observer) throw new Error("no IntersectionObserver is watching that element");
  act(() => {
    observer.callback(
      [{ isIntersecting: true, target: el } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    );
  });
}

// Alpha is the first row, so the layout puts it inside the first screen. Bravo is 100 rows down:
// past the margin even in the narrow layout, where Alpha's height (50 rows) pushes it there.
const layout: DashboardLayout = {
  v: 1,
  range: "24h",
  widgets: [
    { id: "w_alpha", type: "a.one", x: 0, y: 0, w: 4, h: 50, range: null, config: {} },
    { id: "w_bravo", type: "b.two", x: 6, y: 100, w: 6, h: 6, range: null, config: {} },
  ],
};

const view = () =>
  render(<DashboardView layout={layout} renderHeader={(actions) => <div>{actions}</div>} />);

// The grid width decides the column count (1000 → 12 columns, 700 → 6). The rect's top is far below
// the viewport: whether a body is mounted in the first commit must not depend on it.
let gridWidth = 1000;

function box(): DOMRect {
  return {
    width: gridWidth,
    height: 100,
    top: 5000,
    left: 0,
    right: gridWidth,
    bottom: 5100,
    x: 0,
    y: 5000,
  } as DOMRect;
}

beforeEach(() => {
  MockObserver.all = [];
  gridWidth = 1000;
  vi.stubGlobal("IntersectionObserver", MockObserver);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(box);
  // Customize's edit grid reads `--dash-row` / `--dash-gap` to size its rows. The read grid's
  // deferral does not: its pitch is the token constant, and this stub is here for the editor.
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
  } as CSSStyleDeclaration);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the read grid defers widget bodies", () => {
  it("mounts the box the layout puts on screen in the first commit, and holds the one below it", () => {
    const { container } = view();
    const bravo = container.querySelector<HTMLElement>('.dg-item[data-type="b.two"]')!;

    // On screen: the body is in this first render, so its own chunk is not rendered late.
    expect(screen.getByText("Alpha body")).toBeInTheDocument();

    // Below the margin: the box is there at its grid size, holding the frame's title and the
    // existing loading line — no skeleton, and nothing mounted behind it.
    expect(bravo).toHaveAttribute("data-y", "100");
    expect(screen.getByRole("heading", { name: "Bravo" })).toBeInTheDocument();
    expect(screen.getByText("Loading bravo…")).toBeInTheDocument();
    expect(screen.queryByText("Bravo body")).toBeNull();

    // Only the box below is observed; the on-screen one never needs an observer.
    expect(MockObserver.all).toHaveLength(1);
    expect(MockObserver.all[0].observed).toEqual([bravo]);

    intersect(bravo);

    expect(screen.getByText("Bravo body")).toBeInTheDocument();
    expect(screen.queryByText("Loading bravo…")).toBeNull();
  });

  it("mounts every widget below 12 columns, where a short placeholder would shift the page", () => {
    gridWidth = 700;
    view();
    // No intersection was delivered, yet both bodies are mounted. The grid's column count
    // starts at 12 and is corrected from the container width in a layout effect, so the first
    // commit may have observed the box below the margin; that observer is disconnected once
    // the grid drops below 12 columns, and nothing stays watched.
    expect(screen.getByText("Alpha body")).toBeInTheDocument();
    expect(screen.getByText("Bravo body")).toBeInTheDocument();
    expect(screen.queryByText("Loading bravo…")).toBeNull();
    expect(MockObserver.all.every((observer) => observer.disconnected)).toBe(true);
  });

  it("mounts every widget in Customize mode without waiting for the observer", async () => {
    view();
    expect(screen.queryByText("Bravo body")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Customize" }));

    // The edit grid loads and mounts both bodies; no observer entry was ever delivered.
    expect(await screen.findByText("Bravo body")).toBeInTheDocument();
    expect(screen.getByText("Alpha body")).toBeInTheDocument();
  });
});
