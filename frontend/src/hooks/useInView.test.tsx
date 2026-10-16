// useInView: the read grid's visibility gate (UR.8.3). A box starts deferred when the layout puts
// it below the first screen plus the observer's one-viewport margin; a box the layout already puts
// inside that margin is in view from the first render, so its body mounts in the page's own commit
// and never goes through React's reveal throttle. The decision reads the row and `window.innerHeight`
// only — no `getBoundingClientRect`, no `getComputedStyle`. Where IntersectionObserver does not
// exist, or there is no window, everything mounts at once, as the grid did before.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { act, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { cellStartsInView, ROW_PITCH_PX, useInView } from "./useInView";

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
  fire(isIntersecting: boolean) {
    this.callback(
      [{ isIntersecting, target: this.observed[0] } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

/** The box's row in the 12-column grid; null means the box is not deferred at all. */
function Probe({ y }: { y: number | null }) {
  const { ref, inView } = useInView<HTMLDivElement>(y);
  return <div ref={ref} data-testid="box" data-inview={inView ? "yes" : "no"} />;
}

// The pitch is the token, not a stub: `--dash-row` 24px plus `--dash-gap` 12px, rows every 36px.
// jsdom's window is 768px tall, so the first screen plus one viewport of margin reaches 42 rows
// (42 x 36 = 1512px < 2 x 768); row 43 (1548px) is past it.
beforeEach(() => {
  MockObserver.all = [];
  vi.stubGlobal("IntersectionObserver", MockObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useInView", () => {
  it("reads the row pitch from the dashboard tokens, not from the DOM", () => {
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../app/tokens.css"),
      "utf8",
    );
    expect(css).toMatch(/--dash-row:\s*24px/);
    expect(css).toMatch(/--dash-gap:\s*var\(--widget-gap\)/);
    expect(css).toMatch(/--widget-gap:\s*var\(--sp-3\)/);
    expect(css).toMatch(/--sp-3:\s*12px/);
    expect(ROW_PITCH_PX).toBe(36);
  });

  it("is in view from the first render, with no observer and no measurement, inside the margin", () => {
    const rect = vi.spyOn(Element.prototype, "getBoundingClientRect");
    const styles = vi.spyOn(window, "getComputedStyle");
    render(<Probe y={0} />);
    expect(screen.getByTestId("box")).toHaveAttribute("data-inview", "yes");
    // Nothing is left waiting on the observer, and the box is never measured: the layout answered.
    expect(MockObserver.all).toHaveLength(0);
    expect(rect).not.toHaveBeenCalled();
    expect(styles).not.toHaveBeenCalled();
  });

  it("starts out of view below the margin, observes the box there, and mounts it on entry", () => {
    render(<Probe y={100} />);
    const box = screen.getByTestId("box");
    expect(box).toHaveAttribute("data-inview", "no");
    expect(MockObserver.all).toHaveLength(1);
    expect(MockObserver.all[0].options?.rootMargin).toBe("100% 0px");
    expect(MockObserver.all[0].observed).toEqual([box]);

    act(() => MockObserver.all[0].fire(true));
    expect(box).toHaveAttribute("data-inview", "yes");
    // The observer has answered and stops; it does not fire again.
    expect(MockObserver.all[0].disconnected).toBe(true);
  });

  it("puts the boundary one viewport of margin below the viewport, on the row", () => {
    expect(window.innerHeight).toBe(768);
    expect(cellStartsInView(42, window.innerHeight)).toBe(true);
    expect(cellStartsInView(43, window.innerHeight)).toBe(false);

    const shown = render(<Probe y={42} />);
    expect(screen.getByTestId("box")).toHaveAttribute("data-inview", "yes");
    expect(MockObserver.all).toHaveLength(0);

    shown.unmount();
    render(<Probe y={43} />);
    expect(screen.getByTestId("box")).toHaveAttribute("data-inview", "no");
    expect(MockObserver.all).toHaveLength(1);
  });

  it("stays out of view while only entries that do not intersect arrive", () => {
    render(<Probe y={100} />);
    const box = screen.getByTestId("box");
    act(() => MockObserver.all[0].fire(false));
    expect(box).toHaveAttribute("data-inview", "no");
  });

  it("keeps the box mounted once it has been in view", () => {
    render(<Probe y={100} />);
    const box = screen.getByTestId("box");
    act(() => MockObserver.all[0].fire(true));
    act(() => MockObserver.all[0].fire(false));
    expect(box).toHaveAttribute("data-inview", "yes");
  });

  it("mounts at once where IntersectionObserver does not exist", () => {
    vi.unstubAllGlobals();
    // Window stays, so this is not the no-window path. Server render runs no effect, so only
    // the first-render initialiser can answer a row the layout would otherwise defer.
    const html = renderToString(createElement(Probe, { y: 100 }));
    expect(html).toContain('data-inview="yes"');
    expect(MockObserver.all).toHaveLength(0);
  });

  it("mounts at once, and observes nothing, when the box is not deferred at all", () => {
    render(<Probe y={null} />);
    expect(screen.getByTestId("box")).toHaveAttribute("data-inview", "yes");
    expect(MockObserver.all).toHaveLength(0);
  });

  it("renders every row in view when there is no window, even with an observer available", () => {
    expect(cellStartsInView(100, null)).toBe(true);
    const realWindow = globalThis.window;
    // React's server render does not need `window`. The observer stays defined, so this is the
    // no-window path and not the no-IntersectionObserver path.
    vi.stubGlobal("window", undefined);
    try {
      const html = renderToString(createElement(Probe, { y: 100 }));
      expect(html).toContain('data-inview="yes"');
      expect(MockObserver.all).toHaveLength(0);
    } finally {
      vi.stubGlobal("window", realWindow);
    }
  });
});
