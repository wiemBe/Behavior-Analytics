// The dashboard reads the search cadence once for all its widgets (UI-7.2), and each widget's
// frame judges its data time against its own family.

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads, type SearchCadenceItem } from "@/lib/api";

import { DashboardView } from "./DashboardView";
import type { AnyWidgetDef, DashboardLayout } from "./types";

const AT = vi.hoisted(() => ({ iso: "" }));

vi.mock("./registry", async () => {
  const { WidgetFrame } = await import("@/components/home/WidgetFrame");
  const def = (type: string, title: string, family: "PHASE_A_VOLUME" | "WAF_COLLECTION") =>
    ({
      type,
      title,
      description: title,
      category: "Chart",
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
          dataTo={{ at: AT.iso, family }}
        >
          {({ data }) => <p>{data}</p>}
        </WidgetFrame>
      ),
    }) as unknown as AnyWidgetDef;
  const WIDGETS = [
    def("a.one", "Alpha", "PHASE_A_VOLUME"),
    def("b.two", "Bravo", "WAF_COLLECTION"),
    def("c.three", "Charlie", "PHASE_A_VOLUME"),
  ];
  return { WIDGETS, widgetDef: (t: string) => WIDGETS.find((w) => w.type === t) };
});

const layout: DashboardLayout = {
  v: 1,
  range: "24h",
  widgets: ["a.one", "b.two", "c.three"].map((type, i) => ({
    id: `w_${i}aaaa`,
    type,
    x: i * 4,
    y: 0,
    w: 4,
    h: 6,
    range: null,
    config: {},
  })),
};

const item = (family: SearchCadenceItem["family"], cadence: number) =>
  ({ family, cadence_seconds: cadence, bucket_seconds: 300 }) as SearchCadenceItem;

beforeEach(() => {
  AT.iso = new Date(Date.now() - 1200 * 1000).toISOString();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ width: 1000, height: 100, top: 0, left: 0, right: 1000, bottom: 100, x: 0, y: 0 }) as DOMRect,
  );
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  vi.spyOn(window, "getComputedStyle").mockReturnValue({
    getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
  } as CSSStyleDeclaration);
});
afterEach(() => vi.restoreAllMocks());

describe("DashboardView cadence", () => {
  it("makes one cadence request for three widgets and judges each by its family", async () => {
    const fetch = vi.fn(async () => [item("PHASE_A_VOLUME", 300), item("WAF_COLLECTION", 900)]);
    vi.spyOn(homeReads, "searchCadence").mockReturnValue({
      key: "/admin/search-cadence?test=view",
      fetch,
    });
    render(<DashboardView layout={layout} renderHeader={(actions) => <div>{actions}</div>} />);
    // 20 min old: stale for the two Phase A widgets, inside 2 x 15 min for the WAF one.
    await waitFor(() =>
      expect(
        screen.queryAllByText((_c, el) => el?.tagName === "P" && /^Stale:/.test(el.textContent ?? "")),
      ).toHaveLength(2),
    );
    expect(
      screen.queryAllByText((_c, el) => el?.tagName === "P" && /^Data to/.test(el.textContent ?? "")),
    ).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
