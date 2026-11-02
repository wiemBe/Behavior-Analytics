import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { answer } from "./fixtures";
import { GeoMap } from "./GeoMap";
import type { WorldState } from "./world";

const { setOption, init, use, registerMap, dispose } = vi.hoisted(() => {
  const setOption = vi.fn();
  const dispose = vi.fn();
  return {
    setOption,
    dispose,
    registerMap: vi.fn(),
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose, on: vi.fn() })),
    use: vi.fn(),
  };
});
vi.mock("echarts/core", () => ({ init, use, registerMap }));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const ready = (codes: string[]): WorldState => ({
  status: "ok",
  world: { geoJson: { type: "FeatureCollection", features: [] }, codes: new Set(codes) },
});

beforeEach(() => {
  setOption.mockClear();
  init.mockClear();
  registerMap.mockClear();
  dispose.mockClear();
});

describe("GeoMap", () => {
  it("says the outlines are loading, at the plot's size, and creates no chart", () => {
    render(<GeoMap data={answer()} world={{ status: "loading" }} locale="en" ariaLabel="x" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the map outlines…");
    expect(document.querySelector(".geo-plot")).not.toBeNull();
    expect(init).not.toHaveBeenCalled();
  });

  it("says what failed and what to do when the outlines cannot be loaded", () => {
    render(<GeoMap data={answer()} world={{ status: "error" }} locale="en" ariaLabel="x" />);
    expect(screen.getByRole("alert")).toHaveTextContent("The map outlines could not be loaded.");
    expect(screen.getByRole("alert")).toHaveTextContent("The table has the same figures. Reload the page");
    expect(init).not.toHaveBeenCalled();
  });

  it("registers the outlines and draws the countries that have a polygon", () => {
    render(
      <GeoMap data={answer()} world={ready(["US", "DE", "CN"])} locale="en" ariaLabel="A map" />,
    );
    expect(screen.getByRole("img")).toHaveAccessibleName("A map");
    expect(registerMap).toHaveBeenCalledWith("geo-world", expect.anything());
    const option = setOption.mock.calls[0][0] as { series: Array<{ data: Array<{ name: string }> }> };
    expect(option.series[0].data.map((d) => d.name).sort()).toEqual(["CN", "DE", "US"]);
  });

  it("registers the map before it gives the chart the option", () => {
    render(<GeoMap data={answer()} world={ready(["US"])} locale="en" ariaLabel="A map" />);
    expect(registerMap.mock.invocationCallOrder[0]).toBeLessThan(setOption.mock.invocationCallOrder[0]);
  });

  it("disposes the chart when it goes away", () => {
    const { unmount } = render(
      <GeoMap data={answer()} world={ready(["US"])} locale="en" ariaLabel="A map" />,
    );
    unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("words the tooltip in the interface language", () => {
    render(<GeoMap data={answer()} world={ready(["DE"])} locale="en" ariaLabel="A map" />);
    const option = setOption.mock.calls[0][0] as {
      tooltip: { formatter: (p: unknown) => string };
    };
    expect(option.tooltip.formatter({ name: "DE" })).toContain("Germany");
    expect(option.tooltip.formatter({ name: "DE" })).toContain("402,300 events");
    expect(option.tooltip.formatter({ name: "ZW" })).toContain("No data");
  });
});
