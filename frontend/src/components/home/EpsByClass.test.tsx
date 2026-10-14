import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceClass } from "@/lib/api";
import { fleet, fleetClass, noData } from "@/lib/home/fixtures";

import { EpsByClass } from "./EpsByClass";
import { failed, forbidden, loading, ok } from "./testkit";

const { setOption, dispose, init, use, on } = vi.hoisted(() => {
  const setOption = vi.fn();
  const dispose = vi.fn();
  const on = vi.fn();
  return { setOption, dispose, on, init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose, on })), use: vi.fn() };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ PieChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const res = vi.hoisted(() => ({ state: undefined as unknown }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: () => res.state ?? { status: "loading" },
}));

const seven: SourceClass[] = ["FIREWALL", "WAF", "LINUX", "PROXY", "WINDOWS", "DNS", "UNCLASSIFIED"];
const sevenFleet = () =>
  fleet(seven.map((c, i) => fleetClass(c, { mean_eps: 1000 - i * 100 })));
const data = () =>
  (setOption.mock.calls[0][0] as { series: Array<{ data: Array<{ name: string; value: number }> }> })
    .series[0].data;

beforeEach(() => {
  res.state = undefined;
  setOption.mockClear();
  init.mockClear();
  push.mockClear();
  on.mockClear();
});

describe("EpsByClass", () => {
  it("lists class, EPS and share, and the total, as a table", () => {
    res.state = ok(fleet([fleetClass("FIREWALL", { mean_eps: 750 }), fleetClass("WAF", { mean_eps: 250 })]));
    render(<EpsByClass />);
    const table = screen.getByRole("table", { name: /EPS by source class/ });
    const row = within(table).getByRole("link", { name: "Firewall" }).closest("tr")!;
    expect(within(row).getByText("750.00")).toBeInTheDocument();
    expect(within(row).getByText("75 %")).toBeInTheDocument();
    expect(table).toHaveAccessibleName(/Total 1,000.00 EPS/);
    expect(within(table).getByRole("columnheader", { name: "EPS" })).toBeInTheDocument();
  });

  it("hides the donut from assistive technology and puts the total in its hole", () => {
    res.state = ok(sevenFleet());
    const { container } = render(<EpsByClass />);
    const donut = container.querySelector(".hc-donut")!;
    expect(donut).toHaveAttribute("aria-hidden", "true");
    expect(donut).toHaveTextContent("EPS");
  });

  it("never draws more than five slices, however many classes there are", () => {
    res.state = ok(sevenFleet());
    render(<EpsByClass />);
    expect(data()).toHaveLength(5);
    expect(data().at(-1)!.name).toBe("Other classes");
  });

  it("names the classes inside Other classes", () => {
    res.state = ok(sevenFleet());
    render(<EpsByClass />);
    const row = screen.getByRole("link", { name: "Other classes" }).closest("tr")!;
    expect(row).toHaveTextContent(/Windows, DNS, Unclassified/);
  });

  it("links a class to the sources filtered by it and the aggregate to the fleet table", () => {
    res.state = ok(sevenFleet());
    render(<EpsByClass />);
    expect(screen.getByRole("link", { name: "Firewall" })).toHaveAttribute(
      "href",
      "/fleet/sources?class=FIREWALL",
    );
    expect(screen.getByRole("link", { name: "Other classes" })).toHaveAttribute("href", "/fleet");
  });

  it("goes to the class when its slice is clicked", () => {
    res.state = ok(sevenFleet());
    render(<EpsByClass />);
    const handler = on.mock.calls.find((c) => c[0] === "click")![1] as (e: unknown) => void;
    handler({ dataIndex: 1 });
    expect(push).toHaveBeenCalledWith("/fleet/sources?class=WAF");
    handler({ dataIndex: 99 });
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("counts a class with no mean in words and never lists it as zero", () => {
    res.state = ok(fleet([fleetClass("FIREWALL", { mean_eps: 10 }), noData("WAF")]));
    render(<EpsByClass />);
    expect(screen.getByText(/1 class has no complete bucket and is not in the total/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "WAF" })).not.toBeInTheDocument();
    expect(data().map((d) => d.name)).toEqual(["Firewall"]);
  });

  it("is INSUFFICIENT_DATA, with no donut, when no class has a mean", () => {
    res.state = ok(fleet([noData("FIREWALL"), noData("WAF")]));
    render(<EpsByClass />);
    expect(init).not.toHaveBeenCalled();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText(/No class has a complete bucket/)).toBeInTheDocument();
    expect(screen.getByText(/2 classes have no complete bucket and are not in the total/)).toBeInTheDocument();
  });

  it("says Partial with the interval coverage", () => {
    res.state = ok(
      fleet([
        fleetClass("FIREWALL", { buckets: { expected: 48, complete: 40, partial: 4, missing: 4 } }),
      ]),
    );
    render(<EpsByClass />);
    expect(screen.getByText(/Partial: 40 of 48 intervals collected/)).toBeInTheDocument();
  });

  it("renders loading, forbidden and error", () => {
    res.state = loading;
    const a = render(<EpsByClass />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading EPS by class/);
    a.unmount();
    res.state = forbidden;
    const b = render(<EpsByClass />);
    expect(screen.getByText(/read:fleet/)).toBeInTheDocument();
    b.unmount();
    res.state = failed(null);
    render(<EpsByClass />);
    expect(screen.getByRole("alert")).toHaveTextContent(/did not answer/);
    expect(init).not.toHaveBeenCalled();
  });

  it("keeps the figures and says so when a refresh failed", () => {
    res.state = ok(sevenFleet(), { refreshFailed: { httpStatus: 503 } });
    render(<EpsByClass />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed \(HTTP 503\)/)).toBeInTheDocument();
  });
});
