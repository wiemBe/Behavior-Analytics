import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HomePalette } from "@/lib/home/chartPalette";

import {
  consoleHistory,
  emptySeries,
  historyOf,
  seriesOf,
} from "./fixtures";
import { TrendCharts } from "./TrendCharts";

const drawn = vi.hoisted(() => ({
  builds: [] as Array<((p: HomePalette) => unknown) | null>,
}));
vi.mock("@/components/home/useEChart", () => ({
  useEChart: (build: ((p: HomePalette) => unknown) | null) => {
    drawn.builds.push(build);
    return { current: null };
  },
}));
vi.mock("echarts/core", () => ({ init: vi.fn(), use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {}, CustomChart: {} }));
vi.mock("echarts/components", () => ({ GridComponent: {}, TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

beforeEach(() => {
  drawn.builds = [];
});

const show = (console_ = consoleHistory(), hours: 24 | 168 = 24) =>
  render(<TrendCharts history={historyOf([console_], hours)} console_={console_} />);

/** The panel a chart title opens. */
const panel = (title: string) => screen.getByRole("region", { name: title });

describe("TrendCharts: what is drawn", () => {
  it("states the window once in UTC and says a break is missing data", () => {
    show();
    const line = screen.getByText(/^From/);
    expect(line).toHaveTextContent("2026-11-01 10:16:00 UTC");
    expect(line).toHaveTextContent("2026-11-02 10:16:00 UTC");
    expect(line).toHaveTextContent("A break in a line is a stretch with no stored window");
  });

  it("offers the four charts, each under its own title", () => {
    show();
    for (const title of [
      "Reachability and API latency",
      "Fullest mount per host",
      "CRE queue per host",
      "Throttle spill per host",
    ]) {
      expect(panel(title)).toBeInTheDocument();
    }
    // Reachability draws two charts (the band and the latency), the three host charts one each.
    expect(drawn.builds.filter((b) => b !== null)).toHaveLength(5);
  });

  it("gives every canvas a name that says what it shows", () => {
    show();
    expect(screen.getByRole("img", { name: "Share of the fullest mount used, per host, over time" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Reachability of the console as spans of time" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "API latency of the console in milliseconds over time" })).toBeInTheDocument();
  });

  it("names every line in a key with its peak and latest value, in the unit", () => {
    show();
    const key = within(panel("Fullest mount per host")).getByRole("list", { name: "Fullest mount per host" });
    expect(key).toHaveTextContent("qr-console");
    expect(key).toHaveTextContent("peak 43.0 %");
    expect(key).toHaveTextContent("latest 43.0 %");
    const spill = within(panel("Throttle spill per host")).getByRole("list");
    expect(spill).toHaveTextContent("peak 4.00 KiB");
  });

  it("counts the reachable samples", () => {
    show();
    expect(within(panel("Reachability and API latency")).getByText("Reachable in 3 of 4 samples.")).toBeInTheDocument();
  });

  it("builds options only from the stored points: a gap is a null and a zero stays a zero", () => {
    show();
    const options = drawn.builds.filter((b): b is (p: HomePalette) => unknown => b !== null).map((b) =>
      b(
        new Proxy({} as Record<string, string>, { get: () => "c" }) as unknown as HomePalette,
      ) as { series: Array<{ data: Array<[number, number | null]> }> },
    );
    const spill = options.find((o) => o.series[0]?.data?.some?.(([, v]) => v === 4096));
    expect(spill?.series[0].data.map(([, v]) => v)).toEqual([0, 0, 4096, 0]);
  });
});

describe("TrendCharts: series that hold no figure", () => {
  it("says a series with no point is not collected, with the API's reason, instead of drawing a line", () => {
    show(
      consoleHistory({
        hosts: [
          {
            host: "qr-console",
            disk_fullest_fraction: emptySeries("No window in this range holds the disk."),
            cre_queue: seriesOf([1, 2, 3]),
            throttle_spill_bytes: emptySeries("No window in this range holds the spill."),
          },
        ],
      }),
    );
    const disk = panel("Fullest mount per host");
    expect(disk).toHaveTextContent("Not collected");
    expect(disk).toHaveTextContent("No window in this range holds the disk.");
    expect(within(disk).queryByRole("img")).not.toBeInTheDocument();
    expect(within(panel("CRE queue per host")).getByRole("img")).toBeInTheDocument();
    expect(panel("Throttle spill per host")).toHaveTextContent("No window in this range holds the spill.");
  });

  it("says every host chart is not collected when the console has no metric window in the range", () => {
    show(
      consoleHistory({
        hosts: [],
        metrics: { state: "NOT_COLLECTED", reason_code: "no_window_in_range", reason: "No window was stored in this range." },
      }),
    );
    // The three host charts share one cause, so it is said once, not three times.
    const shared = panel("Fullest mount, CRE queue and throttle spill per host");
    expect(shared).toHaveTextContent("No window was stored in this range.");
    expect(within(shared).queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getAllByText("No window was stored in this range.")).toHaveLength(1);
    for (const title of ["Fullest mount per host", "CRE queue per host", "Throttle spill per host"]) {
      expect(screen.queryByRole("region", { name: title })).not.toBeInTheDocument();
    }
  });

  it("says a failed metric part is failed, in the error tone", () => {
    show(
      consoleHistory({
        hosts: [],
        metrics: { state: "FAILED", reason_code: "search_failed", reason: "The search failed." },
      }),
    );
    expect(
      within(panel("Fullest mount, CRE queue and throttle spill per host")).getByText("Failed"),
    ).toHaveClass("crit");
  });

  it("says no health sample was stored when reachability has no point, and draws neither chart for it", () => {
    show(
      consoleHistory({
        reachability: { state: "NOT_COLLECTED", reason_code: "no_window_in_range", reason: null, points: [] },
      }),
    );
    const reach = panel("Reachability and API latency");
    expect(reach).toHaveTextContent("Not collected");
    expect(reach).toHaveTextContent("No health sample was stored in this range.");
    expect(within(reach).queryByRole("img")).not.toBeInTheDocument();
  });

  it("says no latency was measured when every sample lacks one, and still draws the band", () => {
    const base = consoleHistory();
    show(
      consoleHistory({
        reachability: {
          ...base.reachability,
          points: base.reachability.points.map((p) => ({ ...p, api_latency_ms: null })),
        },
      }),
    );
    const reach = panel("Reachability and API latency");
    expect(reach).toHaveTextContent("No latency was measured in this range.");
    expect(within(reach).getAllByRole("img")).toHaveLength(1);
  });

  it("lists the hosts that have no figure beside the ones that do", () => {
    show(
      consoleHistory({
        hosts: [
          ...consoleHistory().hosts,
          {
            host: "ep-02",
            disk_fullest_fraction: emptySeries("ep-02 sent no disk figure."),
            cre_queue: emptySeries(),
            throttle_spill_bytes: emptySeries(),
          },
        ],
      }),
    );
    expect(within(panel("Fullest mount per host")).getByText("ep-02: Not collected. ep-02 sent no disk figure.")).toBeInTheDocument();
  });

  it("says when more hosts exist than the chart has colours for", () => {
    const hosts = Array.from({ length: 7 }, (_, at) => ({
      host: `h${at}`,
      disk_fullest_fraction: seriesOf([0.1 * (at + 1)]),
      cre_queue: seriesOf([at]),
      throttle_spill_bytes: seriesOf([0]),
    }));
    show(consoleHistory({ hosts }));
    const disk = panel("Fullest mount per host");
    expect(within(disk).getAllByRole("listitem")).toHaveLength(5);
    expect(disk).toHaveTextContent("2 more hosts are not drawn, because the chart has 5 distinct colours: h1, h0.");
  });

  it("says there is no trend when the console is not in the answer", () => {
    render(<TrendCharts history={historyOf([])} console_={undefined} />);
    expect(screen.getByText("No trend was returned for this console.")).toBeInTheDocument();
  });
});
