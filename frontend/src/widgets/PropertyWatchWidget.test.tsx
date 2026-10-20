import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { setOption, resize, dispose, init, use, shell } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return {
    setOption,
    resize,
    dispose,
    init: vi.fn(() => ({ setOption, resize, dispose })),
    use: vi.fn(),
    shell: { tick: 0 },
  };
});

// jsdom has no canvas, so ECharts is stubbed; the real `format` is kept because the tooltip
// encoding is part of what is under test.
vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("@/components/shell/AutoRefresh", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/shell/AutoRefresh")>()),
  useRefreshTick: () => shell.tick,
}));

import {
  bucket,
  catalog,
  HOSTILE,
  iso,
  paged,
  point,
  series,
  value,
  watch,
  WATCH_ID,
} from "@/components/propertywatch/fixtures";
import { ApiError, api, type PropertyWatch, type PwBucket, type PwValue } from "@/lib/api";

import { PropertyWatchWidget, PW_WIDGET_FORBIDDEN } from "./PropertyWatchWidget";

class TestResizeObserver {
  observe() {}
  disconnect() {}
}

const vid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
/** 24 h of buckets: every five-minute interval of the window, ending at iso(11). */
const fullDay = (over: Partial<PwBucket> = {}): PwBucket[] =>
  Array.from({ length: 288 }, (_, i) => bucket(11 - 287 + i, over));

interface World {
  watch?: PropertyWatch | Error;
  catalog?: ReturnType<typeof catalog> | Error;
  buckets?: PwBucket[] | Error;
  values?: PwValue[] | Error;
  series?: "from-ids" | Error;
}

function world(w: World = {}) {
  const pick = <T,>(v: T | Error | undefined, dflt: T): Promise<T> =>
    v instanceof Error ? Promise.reject(v) : Promise.resolve(v ?? dflt);
  const values = [
    value(vid(1), "svc-backup", { pinned: true, events: 12 }),
    value(vid(2), "alice", { events: 300 }),
    value(vid(3), "bob", { events: 200 }),
  ];
  vi.spyOn(api, "propertyWatch").mockImplementation(() => pick(w.watch, watch()));
  vi.spyOn(api, "propertyWatchCatalog").mockImplementation(() => pick(w.catalog, catalog()));
  vi.spyOn(api, "propertyWatchBuckets").mockImplementation(() => pick(w.buckets, fullDay()));
  vi.spyOn(api, "propertyWatchValues").mockImplementation(() => {
    const v = w.values;
    return v instanceof Error ? Promise.reject(v) : Promise.resolve(paged(v ?? values));
  });
  vi.spyOn(api, "propertyWatchSeries").mockImplementation((_id, ids) => {
    if (w.series instanceof Error) return Promise.reject(w.series);
    const all = (w.values instanceof Error ? [] : (w.values ?? values)) as PwValue[];
    return Promise.resolve({
      hours: 24,
      series: ids.map((i) => {
        const v = all.find((x) => x.id === i) as PwValue;
        return series(
          i,
          v.value_key,
          Array.from({ length: 12 }, (_, n) => point(n)),
          v.pinned,
        );
      }),
    });
  });
}

function renderWidget(config: { watch_id: string | null } = { watch_id: WATCH_ID }) {
  return render(<PropertyWatchWidget id="w_pw1" config={config} range={null} expanded={false} />);
}

const drill = () => screen.getByRole("link", { name: "Watch" });

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(iso(12))); // three minutes after the last bucket began
  shell.tick = 0;
  setOption.mockClear();
  init.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("chart.property_watch, ok", () => {
  it("draws the pinned value and the top values, with unit, UTC time, coverage pill and drill-down", async () => {
    world();
    renderWidget();
    expect(await screen.findByRole("img", { name: /events per 5 min/ })).toBeInTheDocument();
    expect(api.propertyWatchSeries).toHaveBeenCalledWith(
      WATCH_ID,
      [vid(1), vid(2), vid(3)], // pinned first, then by events
      24,
      expect.anything(),
    );
    const key = screen.getByRole("list", { name: "Chart key" });
    expect(within(key).getByText("svc-backup")).toBeInTheDocument();
    expect(within(key).getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("Coverage 96 %")).toBeInTheDocument();
    expect(screen.getByText("2026-10-18 08:55:00 UTC")).toBeInTheDocument();
    expect(screen.getByText(/Y axis: events per 5 min/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /VPN usernames/ })).toBeInTheDocument();
    expect(drill()).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
    expect(screen.queryByText(/^Stale/)).toBeNull();
    expect(screen.queryByText(/Partial/)).toBeNull();
  });

  it("draws at most five values and never ranks one without a counted event", async () => {
    const many: PwValue[] = [
      value(vid(1), "p1", { pinned: true, events: null, reading: "NO_BUCKET" }),
      ...Array.from({ length: 6 }, (_, i) => value(vid(10 + i), `v${i}`, { events: 100 + i })),
      value(vid(30), "zero", { events: 0, reading: "MEASURED_ZERO" }),
      value(vid(31), "unknown", { events: null, reading: "UNKNOWN" }),
    ];
    world({ values: many });
    renderWidget();
    await screen.findByRole("img");
    const ids = (api.propertyWatchSeries as ReturnType<typeof vi.fn>).mock.calls[0][1] as string[];
    expect(ids).toEqual([vid(1), vid(15), vid(14), vid(13), vid(12)]);
    expect(ids).not.toContain(vid(30));
    expect(ids).not.toContain(vid(31));
    expect(document.querySelectorAll(".pw-key-label")).toHaveLength(5);
  });

  it("hands the chart gaps as gaps: a bucket not in the series is no point, a measured zero is 0", async () => {
    world();
    renderWidget();
    await screen.findByRole("img");
    await waitFor(() => expect(setOption).toHaveBeenCalled());
    const option = setOption.mock.calls[0][0] as {
      series: Array<{ name: string; data: Array<{ value: [number, number | null] }>; connectNulls: boolean }>;
    };
    const line = option.series.find((s) => s.name === "alice");
    expect(line?.connectNulls).toBe(false);
    // 24 h at five minutes is 288 slots; only 12 buckets have a point, the rest are null.
    const values = (line?.data ?? []).map((d) => d.value[1]);
    expect(values.filter((v) => v === null).length).toBeGreaterThan(200);
    expect(values.filter((v) => v === 0)).toHaveLength(0);
  });

  it("marks only the intervals with no stored bucket as not collected", async () => {
    // 300 stored buckets end at the last one, so every slot of the window has its bucket.
    const stored = Array.from({ length: 300 }, (_, i) => bucket(12 - 299 + i));
    world({ buckets: stored });
    const { unmount } = renderWidget();
    await screen.findByRole("img");
    // Every interval of the day is a stored bucket, and the series has points for 12 of them:
    // the rest are values with no reading there, not intervals that were not collected.
    expect(screen.queryByText("Not collected")).toBeNull();
    unmount();
    world({ buckets: stored.filter((_, i) => i < 200 || i > 210) });
    renderWidget();
    await screen.findByRole("img");
    expect(screen.getByText("Not collected")).toBeInTheDocument();
  });

  it("renders a hostile value as text in the key and escapes it in the tooltip", async () => {
    world({
      values: [value(vid(1), HOSTILE, { pinned: true, events: 5 })],
    });
    renderWidget();
    await screen.findByRole("img");
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    await waitFor(() => expect(setOption).toHaveBeenCalled());
    const option = setOption.mock.calls[0][0] as {
      tooltip: { formatter: (p: unknown) => string };
    };
    const html = option.tooltip.formatter([{ dataIndex: 280 }]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("chart.property_watch, qualifiers", () => {
  it("says how many intervals were not collected (PARTIAL) and how many buckets are lower bounds (TRUNCATED)", async () => {
    const buckets = fullDay().slice(4).map((b, i) =>
      i < 3 ? { ...b, values_measurement: "LOWER_BOUND" } : b,
    );
    world({ buckets });
    renderWidget();
    await screen.findByRole("img");
    expect(screen.getByText(/284 of 288 five-minute intervals collected/)).toBeInTheDocument();
    expect(screen.getByText(/gaps, not zero events/)).toBeInTheDocument();
    expect(screen.getByText(/3 of 284 collected buckets are lower bounds/)).toBeInTheDocument();
    expect(screen.getByText(/"at least n"/)).toBeInTheDocument();
  });

  it("writes 'at least' on a coverage that is a lower bound", async () => {
    world({
      watch: watch({
        coverage: { bucket_start: iso(11), total_events: null, parsed_events: 880, ratio: 0.88, measurement: "LOWER_BOUND" },
      }),
    });
    renderWidget();
    expect(await screen.findByText("Coverage at least 88 %")).toBeInTheDocument();
  });
});

describe("chart.property_watch, the other states", () => {
  it("is empty while reading history and does not request chart data", async () => {
    const base = watch();
    world({
      watch: watch({
        status: "READING_HISTORY",
        coverage: null,
        collected_through: null,
        history: { ...base.history, state: "READING", days_requested: 28, days_read: 9 },
      }),
    });
    renderWidget();
    expect(await screen.findByText(/Reading history from QRadar: 9 of 28 days/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(api.propertyWatchBuckets).not.toHaveBeenCalled();
    expect(drill()).toBeInTheDocument();
  });

  it("becomes ok after hand-off and never plots a history point", async () => {
    const base = watch();
    world({
      watch: watch({ history: { ...base.history, state: "COMPLETE", days_requested: 28, days_read: 28 } }),
      buckets: [bucket(0, { origin: "HISTORY" }), ...fullDay()],
    });
    vi.spyOn(api, "propertyWatchSeries").mockResolvedValue({
      hours: 24,
      series: [series(vid(1), "svc-backup", [
        point(0, { origin: "HISTORY", events: 777 }),
        point(11, { origin: "LIVE", events: 111 }),
      ], true)],
    });
    renderWidget();
    expect(await screen.findByRole("img")).toBeInTheDocument();
    await waitFor(() => expect(setOption).toHaveBeenCalled());
    const option = setOption.mock.calls[0][0] as {
      series: Array<{ name: string; data: Array<{ value: [number, number | null] }> }>;
    };
    const line = option.series.find((item) => item.name === "svc-backup");
    expect(line?.data.map((item) => item.value[1])).toContain(111);
    expect(line?.data.map((item) => item.value[1])).not.toContain(777);
  });

  it("is not configured: says so, and links to the list", () => {
    world();
    renderWidget({ watch_id: null });
    expect(screen.getByText("Choose a property watch.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watch" })).toHaveAttribute("href", "/behavior/watches");
    expect(api.propertyWatch).not.toHaveBeenCalled();
  });

  it("loading: text only, the drill-down is there", () => {
    world();
    renderWidget();
    expect(screen.getByText("Loading property watches…")).toBeInTheDocument();
    expect(drill()).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
  });

  it("stale by age: shows the last bucket's time and age, still draws", async () => {
    vi.setSystemTime(new Date(Date.parse(iso(11)) + 3 * 3600_000));
    world();
    renderWidget();
    expect(await screen.findByText(/Stale: the latest data is from/)).toHaveTextContent("08:55:00 UTC, 3 h old");
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(drill()).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
  });

  it("is not stale at 3 h old when the watch's cadence is 8 h, and is past 16 h", async () => {
    vi.setSystemTime(new Date(Date.parse(iso(11)) + 3 * 3600_000));
    world({ watch: watch({ cadence_seconds: 8 * 3600 }) });
    renderWidget();
    expect(await screen.findByText(/Last bucket/)).toBeInTheDocument();
    expect(screen.queryByText(/Stale: the latest data is from/)).toBeNull();
  });

  it("is stale past twice an 8 h cadence", async () => {
    vi.setSystemTime(new Date(Date.parse(iso(11)) + 17 * 3600_000));
    world({ watch: watch({ cadence_seconds: 8 * 3600 }) });
    renderWidget();
    expect(await screen.findByText(/Stale: the latest data is from/)).toHaveTextContent("17 h old");
  });

  it("stale by the backend's own STALE status, although the bucket is young", async () => {
    world({ watch: watch({ status: "STALE" }) });
    renderWidget();
    expect(await screen.findByText(/Stale: the latest data is from/)).toBeInTheDocument();
  });

  it("empty: never collected, says why and when the watch was created", async () => {
    world({ watch: watch({ coverage: null, status: null, collected_through: null }) });
    renderWidget();
    expect(await screen.findByText(/No bucket has been collected for this watch yet/)).toHaveTextContent(
      "Created 2026-10-10 09:00:00 UTC",
    );
    expect(screen.getByText(/has not run yet/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(api.propertyWatchValues).not.toHaveBeenCalled();
    expect(drill()).toBeInTheDocument();
  });

  it("empty: a disabled watch says it is disabled", async () => {
    world({ watch: watch({ coverage: null, status: "DISABLED", enabled: false }) });
    renderWidget();
    expect(await screen.findByText(/The watch is disabled/)).toBeInTheDocument();
  });

  it("empty: no value is tracked, and it is not a zero", async () => {
    world({ values: [] });
    renderWidget();
    expect(await screen.findByText(/No value is tracked for this watch/)).toBeInTheDocument();
    expect(api.propertyWatchSeries).not.toHaveBeenCalled();
  });

  it("empty: nothing pinned and nothing counted says there is nothing to draw", async () => {
    world({ values: [value(vid(1), "a", { events: null, reading: "UNKNOWN" })] });
    renderWidget();
    expect(await screen.findByText(/nothing to draw/)).toHaveTextContent("not a reading of zero events");
    expect(api.propertyWatchSeries).not.toHaveBeenCalled();
  });

  it("no_data: NOT_APPLICABLE says the property parsed on nothing, with the coverage", async () => {
    world({
      watch: watch({
        status: "NOT_APPLICABLE",
        coverage: { bucket_start: iso(11), total_events: 1000, parsed_events: 0, ratio: 0, measurement: "EXACT" },
      }),
    });
    renderWidget();
    const text = await screen.findByText(/parsed on no event/);
    expect(text).toHaveTextContent("0 of 1,000 events in scope");
    expect(text).toHaveTextContent("coverage 0 %");
    expect(screen.getByText("No data.")).toBeInTheDocument();
    expect(drill()).toBeInTheDocument();
  });

  it("not_collected: names PW_COLLECTION_ENABLED when the switch is off", async () => {
    world({ catalog: catalog({ collection_enabled: false }) });
    renderWidget();
    expect(await screen.findByText(/PW_COLLECTION_ENABLED is false/)).toBeInTheDocument();
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(drill()).toBeInTheDocument();
  });

  it.each([
    ["SCOPE_STALE" as const, /older than the staleness limit/],
    ["SCOPE_TOO_LARGE" as const, /more searches per run than one watch may use/],
  ])("not_collected: %s names the scope reason", async (status, text) => {
    world({ watch: watch({ status, status_reason: null }) });
    renderWidget();
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
  });

  it("error: the watch read failed, says what failed and what to do, keeps the link", async () => {
    world({ watch: new ApiError(500, "boom") });
    renderWidget();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The property watch could not be read");
    expect(alert).toHaveTextContent("HTTP 500");
    expect(alert).toHaveTextContent("Use Refresh");
    expect(alert).not.toHaveTextContent("boom");
    expect(drill()).toBeInTheDocument();
  });

  it("error: the series read failed", async () => {
    world({ series: new ApiError(502, "x") });
    renderWidget();
    expect(await screen.findByText(/The series could not be read/)).toBeInTheDocument();
    expect(screen.getByText(/HTTP 502/)).toBeInTheDocument();
  });

  it("error on a refresh: the previous answer stays and the widget says so", async () => {
    world();
    const { rerender } = renderWidget();
    await screen.findByRole("img");
    vi.spyOn(api, "propertyWatchBuckets").mockRejectedValue(new ApiError(503, "x"));
    shell.tick = 1;
    rerender(<PropertyWatchWidget id="w_pw1" config={{ watch_id: WATCH_ID }} range={null} expanded={false} />);
    expect(await screen.findByText(/The latest refresh failed/)).toBeInTheDocument();
    expect(screen.getByRole("img")).toBeInTheDocument();
  });

  it("forbidden: names read:property_watches and who grants it, keeps the link", async () => {
    world({ watch: new ApiError(403, "no") });
    renderWidget();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(PW_WIDGET_FORBIDDEN);
    expect(alert).toHaveTextContent("(permission read:property_watches)");
    expect(alert).toHaveTextContent("An administrator grants read:property_watches");
    expect(drill()).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
  });

  it("forbidden on the values read is the same state", async () => {
    world({ values: new ApiError(403, "no") });
    renderWidget();
    expect(await screen.findByRole("alert")).toHaveTextContent("(permission read:property_watches)");
  });

  it("deleted: the chosen watch no longer exists", async () => {
    world({ watch: new ApiError(404, "gone") });
    renderWidget();
    expect(await screen.findByText("This property watch was deleted.")).toBeInTheDocument();
    expect(drill()).toBeInTheDocument();
  });
});
