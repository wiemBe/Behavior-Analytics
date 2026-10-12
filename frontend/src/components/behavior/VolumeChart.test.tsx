import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VolumeChart } from "./VolumeChart";
import type { MetricBucket } from "@/lib/api";

// jsdom has no layout engine or canvas, so ECharts is stubbed. What is under
// test is the contract with it: modular registration, one init, a resize
// observer, disposal on unmount, and — most importantly — the series data,
// where an unobserved interval must arrive as a null rather than a value.
//
// The stubs are hoisted because the component calls `echarts.use(...)` at
// module scope, which runs before a plain `const` in this file is initialized.
const { setOption, resize, dispose, init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return {
    setOption,
    resize,
    dispose,
    init: vi.fn(() => ({ setOption, resize, dispose })),
    use: vi.fn(),
  };
});

vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

// `echarts.use(...)` runs once, when the component module is imported — which
// is before any beforeEach clears the spies. The registration is captured here,
// at module scope, so the assertion below still has it.
const registered = (use.mock.calls[0]?.[0] ?? []) as unknown[];

let observerCallback: (() => void) | null = null;
const observe = vi.fn();
const disconnect = vi.fn();

class TestResizeObserver {
  constructor(cb: () => void) {
    observerCallback = cb;
  }
  observe = observe;
  unobserve = vi.fn();
  disconnect = disconnect;
}

function bucket(over: Partial<MetricBucket> = {}): MetricBucket {
  return {
    bucket_start: "2026-07-20T10:00:00Z",
    bucket_seconds: 300,
    event_count: 600,
    average_eps: 2,
    peak_eps: 2.4,
    completeness: "COMPLETE",
    last_event_at: null,
    ...over,
  };
}

/** The observed series ECharts was handed, as [timestamp, value] pairs. */
function observedSeries(): Array<[number, number | null]> {
  const option = setOption.mock.calls[0][0] as {
    series: Array<{ name: string; data: Array<[number, number | null]> }>;
  };
  return option.series.find((s) => s.name === "Observed EPS")!.data;
}

interface StubSeries {
  name: string;
  data: unknown[];
  connectNulls?: boolean;
  markArea?: unknown;
  markLine?: unknown;
}

function seriesNamed(name: string): StubSeries {
  const option = setOption.mock.calls[0][0] as { series: StubSeries[] };
  return option.series.find((s) => s.name === name)!;
}

beforeEach(() => {
  vi.clearAllMocks();
  observerCallback = null;
  globalThis.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;
});

describe("modular imports", () => {
  it("registers only the chart pieces it uses", () => {
    // The full echarts bundle costs ~340 kB per route; this asserts a modular
    // registration happened rather than a blanket `import * as echarts`.
    expect(registered).toHaveLength(7);
  });
});

describe("lifecycle", () => {
  it("initializes exactly one chart", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(init).toHaveBeenCalledTimes(1);
    expect(setOption).toHaveBeenCalledTimes(1);
  });

  it("observes its container for resize", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(observe).toHaveBeenCalled();
    observerCallback?.();
    expect(resize).toHaveBeenCalled();
  });

  it("disposes the chart and the observer on unmount", () => {
    const view = render(
      <VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />,
    );
    view.unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it("does not initialize a chart when there is nothing to plot", () => {
    render(<VolumeChart buckets={[]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(init).not.toHaveBeenCalled();
  });
});

describe("missing and partial data", () => {
  it("plots a fully observed bucket's value", () => {
    render(
      <VolumeChart
        buckets={[bucket({ average_eps: 2.5 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBe(2.5);
  });

  it("plots a partial bucket as null, not as its undercount", () => {
    render(
      <VolumeChart
        buckets={[bucket({ completeness: "PARTIAL", average_eps: 0.4 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBeNull();
  });

  it("plots a missing bucket as null, not as zero", () => {
    render(
      <VolumeChart
        buckets={[bucket({ completeness: "MISSING", average_eps: 0 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBeNull();
  });

  it("plots an observed zero as zero", () => {
    // A source seen sending nothing is a measurement and a finding; it must
    // not be erased along with the intervals nobody observed.
    render(
      <VolumeChart
        buckets={[bucket({ average_eps: 0, event_count: 0 })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    expect(observedSeries()[0][1]).toBe(0);
  });

  it("never connects the line across a null", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(seriesNamed("Observed EPS").connectNulls).toBe(false);
  });

  it("shades intervals that were not fully observed", () => {
    render(
      <VolumeChart
        buckets={[bucket({ completeness: "PARTIAL" })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    const area = seriesNamed("Observed EPS").markArea as { data: unknown[] };
    expect(area.data).toHaveLength(1);
  });

  it("tells the reader that a gap is not zero traffic", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(screen.getByText(/They are not zero traffic/i)).toBeInTheDocument();
  });

  it("calls an empty window an absence of collection, not zero traffic", () => {
    render(<VolumeChart buckets={[]} expected={2} expectedLow={null} expectedHigh={null} />);
    expect(screen.getByText(/absence of collection, not an observation of zero/i)).toBeInTheDocument();
  });
});

describe("expected band", () => {
  it("draws the expected range as one band and the median as one line", () => {
    render(
      <VolumeChart buckets={[bucket()]} expected={2} expectedLow={1.7} expectedHigh={2.3} />,
    );
    expect(seriesNamed("Expected EPS").data).toHaveLength(1);
    // The band is a stacked pair: an invisible floor at the lower bound, and a
    // filled series whose height is the width of the range.
    const floor = seriesNamed("Expected low") as StubSeries & { stack?: string };
    const band = seriesNamed("Expected range") as StubSeries & {
      stack?: string;
      areaStyle?: unknown;
    };
    expect(floor.stack).toBeDefined();
    expect(floor.stack).toBe(band.stack);
    expect(floor.data[0]).toEqual([expect.any(Number), 1.7]);
    expect((band.data[0] as [number, number])[1]).toBeCloseTo(0.6);
    expect(band.areaStyle).toBeDefined();
    // The old three-line form must not come back.
    expect(setOption.mock.calls[0][0].series.map((s: StubSeries) => s.name)).not.toContain(
      "Expected high",
    );
  });

  it("draws no band when either bound is missing", () => {
    render(
      <VolumeChart buckets={[bucket()]} expected={2} expectedLow={1.7} expectedHigh={null} />,
    );
    expect(seriesNamed("Expected low").data).toHaveLength(0);
    expect(seriesNamed("Expected range").data).toHaveLength(0);
  });

  it("draws no expected line when there is no baseline to draw", () => {
    // An unbaselined source has no expectation. A flat line at 0 would invent
    // one and make any traffic at all look like a spike against it.
    render(
      <VolumeChart buckets={[bucket()]} expected={null} expectedLow={null} expectedHigh={null} />,
    );
    expect(seriesNamed("Expected EPS").data).toHaveLength(0);
  });
});

describe("drawing style", () => {
  const render1 = (extra: Partial<Parameters<typeof VolumeChart>[0]> = {}) =>
    render(
      <VolumeChart
        buckets={[bucket(), bucket({ bucket_start: "2026-07-20T10:05:00Z" })]}
        expected={2}
        expectedLow={1.7}
        expectedHigh={2.3}
        {...extra}
      />,
    );

  it("draws a clean line, with no marker on every point", () => {
    render1();
    expect((seriesNamed("Observed EPS") as { showSymbol?: boolean }).showSymbol).toBe(false);
  });

  it("marks a lone measured bucket, which a line cannot show", () => {
    render(
      <VolumeChart
        buckets={[
          bucket({ completeness: "MISSING" }),
          bucket({ bucket_start: "2026-07-20T10:05:00Z" }),
          bucket({ bucket_start: "2026-07-20T10:10:00Z", completeness: "PARTIAL" }),
        ]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
      />,
    );
    const marks = (seriesNamed("Observed EPS") as { markPoint?: { data: unknown[] } }).markPoint;
    expect(marks?.data).toHaveLength(1);
  });

  it("marks nothing when every measured point is part of a line", () => {
    render1();
    expect((seriesNamed("Observed EPS") as { markPoint?: unknown }).markPoint).toBeUndefined();
  });

  it("takes every colour from the tokens, never from a literal", () => {
    // jsdom has no stylesheet, so each token resolves to a recognisable
    // stand-in. If any colour in the option were a literal, it would show up
    // here as a hex or rgb() value.
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: (name: string) => `token(${name})`,
    } as unknown as CSSStyleDeclaration);
    render1({
      buckets: [
        bucket(),
        bucket({ bucket_start: "2026-07-20T10:05:00Z", completeness: "PARTIAL" }),
      ],
      anomalies: [{ start: "2026-07-20T10:00:00Z", end: null }],
      transitions: [{ at: "2026-07-20T10:05:00Z", label: "OPEN" }],
    });
    const json = JSON.stringify(setOption.mock.calls[0][0]);
    expect(json).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
    expect(json).toContain("token(--chart-observed)");
    expect(json).toContain("token(--chart-band)");
    expect(json).toContain("token(--chart-gap)");
    expect(json).toContain("token(--chart-anomaly)");
  });

  it("does not colour the expected range green", () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: (name: string) => `token(${name})`,
    } as unknown as CSSStyleDeclaration);
    render1();
    expect(JSON.stringify(seriesNamed("Expected range"))).not.toContain("--ok");
    expect(JSON.stringify(seriesNamed("Expected EPS"))).not.toContain("--ok");
  });
});

describe("axes", () => {
  it("carries the unit on the value axis and the timezone on the time axis", () => {
    render(<VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />);
    const option = setOption.mock.calls[0][0] as {
      useUTC: boolean;
      xAxis: { name: string };
      yAxis: { name: string; min: number };
    };
    expect(option.yAxis.name).toMatch(/EPS/);
    expect(option.xAxis.name).toMatch(/UTC/);
    expect(option.useUTC).toBe(true);
    // A drawn zero is a measurement; the axis must be able to show one.
    expect(option.yAxis.min).toBe(0);
  });
});

describe("key", () => {
  const keyItems = () =>
    within(screen.getByRole("list", { name: "Chart key" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent);

  it("names only the marks that were drawn", () => {
    render(<VolumeChart buckets={[bucket()]} expected={null} expectedLow={null} expectedHigh={null} />);
    expect(keyItems()).toEqual(["Observed EPS"]);
  });

  it("names the band, the median, the gaps and the overlays when present", () => {
    render(
      <VolumeChart
        buckets={[bucket(), bucket({ bucket_start: "2026-07-20T10:05:00Z", completeness: "PARTIAL" })]}
        expected={2}
        expectedLow={1.7}
        expectedHigh={2.3}
        anomalies={[{ start: "2026-07-20T10:00:00Z", end: null }]}
        transitions={[{ at: "2026-07-20T10:05:00Z", label: "OPEN" }]}
      />,
    );
    expect(keyItems()).toEqual([
      "Observed EPS",
      "Expected median",
      "Expected range",
      "Not fully collected",
      "Anomalous interval",
      "Lifecycle transition",
    ]);
  });
});

describe("overlays", () => {
  it("shades the anomalous interval", () => {
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        anomalies={[{ start: "2026-07-20T10:00:00Z", end: "2026-07-20T10:05:00Z" }]}
      />,
    );
    const area = seriesNamed("Observed EPS").markArea as { data: unknown[] };
    expect(area.data).toHaveLength(1);
  });

  it("extends a still-running anomaly to the last observation", () => {
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        anomalies={[{ start: "2026-07-20T10:00:00Z", end: null }]}
      />,
    );
    const area = seriesNamed("Observed EPS").markArea as {
      data: Array<[{ xAxis: number }, { xAxis: number }]>;
    };
    expect(area.data[0][1].xAxis).toBe(Date.parse("2026-07-20T10:00:00Z"));
  });

  it("marks lifecycle transitions on the timeline", () => {
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        transitions={[
          { at: "2026-07-20T10:00:00Z", label: "OPEN" },
          { at: "2026-07-20T10:05:00Z", label: "RESOLVED" },
        ]}
      />,
    );
    const line = seriesNamed("Observed EPS").markLine as { data: unknown[] };
    expect(line.data).toHaveLength(2);
  });
});

describe("accessibility", () => {
  // ECharts draws into a canvas, which is opaque to assistive technology. The
  // contract is therefore that the chart is named, and that nothing it is the
  // only carrier of is critical — the numbers behind it are also available as
  // text.
  it("exposes the chart as a named image rather than an unlabelled box", () => {
    const { container } = render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        ariaLabel="Observed EPS for LAB Firewall against its expected baseline"
      />,
    );

    const figure = container.querySelector(".chart")!;
    expect(figure).toHaveAttribute("role", "img");
    expect(figure).toHaveAccessibleName(
      "Observed EPS for LAB Firewall against its expected baseline",
    );
  });

  it("names the chart even when the caller passes no label", () => {
    const { container } = render(
      <VolumeChart buckets={[bucket()]} expected={2} expectedLow={null} expectedHigh={null} />,
    );
    expect(container.querySelector(".chart")).toHaveAccessibleName(/\S/);
  });

  it("carries the reading of the chart as text, not only as a drawing", () => {
    // The summary is what a screen-reader user gets instead of the line. Its
    // wording is tested in `summarizeSourceTimeline`; what matters here is
    // that it reaches the document.
    render(
      <VolumeChart
        buckets={[bucket()]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        textSummary="Over the last 6 hours the source averaged 2.00 EPS against an expected 2.00."
      />,
    );

    expect(
      screen.getByText(/averaged 2.00 EPS against an expected 2.00/),
    ).toBeInTheDocument();
  });

  it("does not communicate a gap by line shape alone", () => {
    // A missing interval is drawn as a break in the line. Sighted or not, the
    // reader must also be told in words that the break is uncollected data
    // rather than measured silence.
    render(
      <VolumeChart
        buckets={[bucket(), bucket({ bucket_start: "2026-07-20T10:05:00Z", completeness: "MISSING" })]}
        expected={2}
        expectedLow={null}
        expectedHigh={null}
        textSummary="1 interval was not collected and is drawn as a gap, not as zero traffic."
      />,
    );

    expect(screen.getByText(/not collected/)).toBeInTheDocument();
    expect(screen.getByText(/not as zero traffic/)).toBeInTheDocument();
  });

  it("adds no accessible noise when there is nothing to plot", () => {
    // An empty window must not leave a named but meaningless image behind.
    const { container } = render(
      <VolumeChart buckets={[]} expected={null} expectedLow={null} expectedHigh={null} />,
    );
    expect(container.querySelector('[role="img"]')).toBeNull();
  });
});
