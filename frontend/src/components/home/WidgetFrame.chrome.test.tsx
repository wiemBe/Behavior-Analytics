// The widget frame's title row (T-098): the pill goes to the body only when the row would not
// fit one line, every control in the row is a 24 px target, and one link style.

import { act, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import { WidgetFrame, type WidgetVariant } from "./WidgetFrame";

// --- measured widths -------------------------------------------------------------------
// jsdom lays nothing out: the row's width and its children's are set by hand. The title row
// is 100 + 100 + 100 px wide when everything shares a line, with the pill in the head or the body.
const widths = { avail: 600, title: 100, pill: 100, tools: 100 };
let observers: Array<() => void> = [];

beforeEach(() => {
  Object.assign(widths, { avail: 600, title: 100, pill: 100, tools: 100 });
  observers = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(cb: () => void) {
        observers.push(cb);
      }
      observe() {}
      disconnect() {}
    },
  );
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains("tw-head") ? widths.avail : 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.classList.contains("pill")) return widths.pill;
      if (this.classList.contains("tw-title")) return widths.title;
      if (this.classList.contains("tw-tools")) return widths.tools;
      return 0;
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, "clientWidth");
  Reflect.deleteProperty(HTMLElement.prototype, "offsetWidth");
});

const runtime: WidgetRuntime = {
  rangeOptions: ["1h", "24h", "7d"],
  override: null,
  dashboardRange: "24h",
  setRange: vi.fn(),
  onExpand: vi.fn(),
  expanded: false,
};

function frame(variant: WidgetVariant = "panel", badge: string | null = "Partial: 278 of 281") {
  return render(
    <WidgetRuntimeContext.Provider value={runtime}>
      <WidgetFrame
        id="f"
        title="POST /api/auth/login"
        windowText="24 h"
        badge={badge ?? undefined}
        link={{ href: "/waf", label: "Endpoints" }}
        what="the endpoint"
        permission="read:waf"
        state={{ kind: "ok", data: "answer", asOf: null }}
        variant={variant}
      >
        {({ data }) => <p>{data}</p>}
      </WidgetFrame>
    </WidgetRuntimeContext.Provider>,
  );
}

const inHead = (c: HTMLElement) => c.querySelector(".tw-head > .pill") !== null;
const inBody = (c: HTMLElement) => c.querySelector(".tw-body > .tw-pill") !== null;
const resize = (avail: number) => {
  widths.avail = avail;
  act(() => observers.forEach((cb) => cb()));
};

describe("the status pill", () => {
  it("stays beside a short title that fits the row", () => {
    const { container } = frame();
    expect(inHead(container)).toBe(true);
    expect(inBody(container)).toBe(false);
  });

  it("moves to the first line of the body when the row would wrap", () => {
    widths.avail = 250; // 100 + 100 + 100 + 2 gaps does not fit
    const { container } = frame();
    expect(inHead(container)).toBe(false);
    expect(container.querySelector(".tw-body")?.firstElementChild).toHaveClass("pill", "tw-pill");
  });

  it("is decided by fit, not by width: a wide row with a long title strips, a narrow one with a short title does not", () => {
    widths.avail = 900;
    widths.title = 800;
    expect(inBody(frame().container)).toBe(true);
    widths.avail = 260;
    widths.title = 40;
    widths.pill = 60;
    widths.tools = 60;
    expect(inHead(frame().container)).toBe(true);
  });

  it("flips once across the threshold and does not flip back and forth", () => {
    // Everything on one line needs 300 px with the pill in either place.
    widths.avail = 299;
    const { container } = frame();
    expect(inBody(container)).toBe(true);
    for (let i = 0; i < 3; i += 1) {
      resize(299);
      expect(inBody(container)).toBe(true);
    }
    resize(300);
    expect(inHead(container)).toBe(true);
    for (let i = 0; i < 3; i += 1) {
      resize(300);
      expect(inHead(container)).toBe(true);
    }
    resize(299);
    expect(inBody(container)).toBe(true);
  });

  it("is never moved on a KPI card or when there is no pill", () => {
    widths.avail = 10;
    expect(inBody(frame("kpi").container)).toBe(false);
    expect(frame("panel", null).container.querySelector(".pill")).toBeNull();
  });
});

describe("hit targets", () => {
  const css = readFileSync(join(__dirname, "../../widgets/dashboard.css"), "utf8");
  const tokens = readFileSync(join(__dirname, "../../app/tokens.css"), "utf8");
  const ruleFor = (selector: string, property: string) =>
    [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^}]*)\}/g)].some(
      ([, selectors, body]) =>
        selectors.split(",").some((s) => s.trim() === selector) && body.includes(property),
    );

  it("defines the 24 px minimum as a token", () => {
    expect(tokens).toMatch(/--target-min:\s*24px;/);
  });

  it.each([".tw-expand", ".wr-preset", ".wr-select", ".tw-all", ".kpi-link", ".dg-arrange-trigger"])(
    "%s has the minimum height and width through the shared rule",
    (selector) => {
      expect(ruleFor(selector, "min-height: var(--target-min)")).toBe(true);
      expect(ruleFor(selector, "min-width: var(--target-min)")).toBe(true);
    },
  );

  it("renders the title row's controls with those classes", () => {
    const { container } = frame();
    const head = container.querySelector(".tw-head")!;
    expect(head.querySelector("button.tw-expand")).not.toBeNull();
    expect(head.querySelector("button.wr-preset")).not.toBeNull();
    expect(head.querySelector("select.wr-select")).not.toBeNull();
    expect(head.querySelector("a.tw-all")).not.toBeNull();
  });
});

describe("the drill-down link", () => {
  const css = readFileSync(join(__dirname, "../../widgets/dashboard.css"), "utf8");

  it("is underlined in a panel and in a KPI card by one rule", () => {
    const m = css.match(/\.tw \.tw-all,\s*\.kpi \.kpi-link\s*\{([^}]*)\}/);
    expect(m?.[1]).toContain("text-decoration: underline");
  });

  it("keeps the underline on hover", () => {
    const m = css.match(/\.tw \.tw-all:hover,\s*\.kpi \.kpi-link:hover\s*\{([^}]*)\}/);
    expect(m?.[1]).toContain("text-decoration: underline");
  });
});

describe("the danger colour", () => {
  it("is not an undefined token", () => {
    const css = readFileSync(join(__dirname, "../../widgets/dashboard.css"), "utf8");
    expect(css).not.toContain("var(--danger)");
    expect(css).toMatch(/\.dg-arrange-remove\s*\{[^}]*var\(--crit-text\)/);
  });
});
