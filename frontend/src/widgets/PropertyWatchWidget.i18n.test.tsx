// chart.property_watch in Turkish (D-37): every state the widget has, with the Turkish number
// form, the Turkish unit of its axis and the honesty states. The harness is the English test's.

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { setOption, init, use, shell } = vi.hoisted(() => {
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
  iso,
  paged,
  point,
  series,
  value,
  watch,
  WATCH_ID,
} from "@/components/propertywatch/fixtures";
import { ApiError, api, type PropertyWatch, type PwBucket, type PwValue } from "@/lib/api";

import { setTestLocale } from "../../vitest.setup";

import { PropertyWatchWidget } from "./PropertyWatchWidget";

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

const drill = () => screen.getByRole("link", { name: "İzleme" });

beforeEach(() => {
  setTestLocale("tr");
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

describe("chart.property_watch in Turkish", () => {
  it("draws the chart with the Turkish unit, the UTC time, the age, the coverage pill and the link", async () => {
    world();
    renderWidget();
    expect(await screen.findByRole("img", { name: /5\u00a0dk başına olay.*UTC/ })).toBeInTheDocument();
    expect(screen.getByText("Kapsama %96")).toBeInTheDocument();
    expect(screen.getByText("2026-10-18 08:55:00 UTC")).toBeInTheDocument();
    expect(screen.getByText(/Y ekseni: 5 dk başına olay\./)).toBeInTheDocument();
    expect(screen.getByText(/\(\d+ dk önce\)/)).toBeInTheDocument();
    expect(screen.getByText("24 sa")).toBeInTheDocument();
    expect(drill()).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
    expect(screen.queryByText(/^Güncel değil/)).toBeNull();
  });

  it("says PARTIAL and TRUNCATED in Turkish, with the counts in the Turkish form", async () => {
    const buckets = fullDay().slice(4).map((b, i) => (i < 3 ? { ...b, values_measurement: "LOWER_BOUND" as const } : b));
    world({ buckets });
    renderWidget();
    expect(await screen.findByText(/284 \/ 288 beş dakikalık aralık toplandı/)).toBeInTheDocument();
    expect(screen.getByText(/kalanlar boşluktur, sıfır olay değildir/)).toBeInTheDocument();
    expect(screen.getByText(/Toplanan zaman dilimlerinden 3 \/ 284 alt sınırdır/)).toBeInTheDocument();
    expect(screen.getByText(/"en az n" olarak okunur/)).toBeInTheDocument();
    expect(screen.getByText("Kısmi")).toBeInTheDocument();
    expect(screen.getByText("Kırpılmış")).toBeInTheDocument();
  });

  it("is not configured: says so and links to the list", () => {
    world();
    renderWidget({ watch_id: null });
    expect(screen.getByText("Bir özellik izleme seçin.")).toBeInTheDocument();
    expect(screen.getByText(/Seçmek için Özelleştir'i, ardından Yapılandır'ı açın\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "İzleme" })).toHaveAttribute("href", "/behavior/watches");
  });

  it("says loading in Turkish and keeps the link", () => {
    world();
    renderWidget();
    expect(screen.getByText("Özellik izlemeleri yükleniyor…")).toBeInTheDocument();
    expect(drill()).toBeInTheDocument();
  });

  it("is stale by age: shows the last bucket's UTC time and its age in Turkish, and still draws", async () => {
    vi.setSystemTime(new Date(Date.parse(iso(11)) + 3 * 3600_000));
    world();
    renderWidget();
    const note = await screen.findByText(/Güncel değil: son veri/);
    expect(note).toHaveTextContent("08:55:00 UTC tarihli, 3 sa önce");
    expect(screen.getByRole("img")).toBeInTheDocument();
  });

  it("says empty when never collected: why, and when the watch was created", async () => {
    world({ watch: watch({ coverage: null, status: null, collected_through: null }) });
    renderWidget();
    const text = await screen.findByText(/Bu izleme için henüz zaman dilimi toplanmadı/);
    expect(text).toHaveTextContent("Oluşturulma: 2026-10-10 09:00:00 UTC");
    expect(screen.getByText(/Henüz çalışmadı/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("says a disabled watch is disabled", async () => {
    world({ watch: watch({ coverage: null, status: "DISABLED", enabled: false }) });
    renderWidget();
    expect(await screen.findByText(/İzleme devre dışı olduğu için/)).toBeInTheDocument();
  });

  it("says no value is tracked, and that nothing to draw is not a zero", async () => {
    world({ values: [] });
    const view = renderWidget();
    expect(await screen.findByText(/Bu izleme için son 24 sa içinde izlenen değer yok/)).toHaveTextContent("Oluşturulma:");
    view.unmount();
    world({ values: [value(vid(1), "a", { events: null, reading: "UNKNOWN" })] });
    renderWidget();
    expect(await screen.findByText(/çizilecek bir şey yok/)).toHaveTextContent("sıfır olay ölçümü değildir");
  });

  it("says NO_DATA for a property that parsed on no event, with Turkish numbers and percent", async () => {
    world({
      watch: watch({
        status: "NOT_APPLICABLE",
        coverage: { bucket_start: iso(11), total_events: 1000, parsed_events: 0, ratio: 0, measurement: "EXACT" },
      }),
    });
    renderWidget();
    const text = await screen.findByText(/özellik hiçbir olayda ayrıştırılamadı/);
    expect(text).toHaveTextContent("(kapsamdaki 0 / 1.000 olay)");
    expect(text).toHaveTextContent("kapsama oranı %0");
    expect(screen.getByText("Veri yok.")).toBeInTheDocument();
  });

  it("says NOT_COLLECTED, naming the switch, in Turkish", async () => {
    world({ catalog: catalog({ collection_enabled: false }) });
    renderWidget();
    expect(await screen.findByText(/PW_COLLECTION_ENABLED false olduğu için/)).toBeInTheDocument();
    expect(screen.getByText("Toplanmadı.")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("says what failed and what to do, with the status, and never the exception", async () => {
    world({ watch: new ApiError(500, "boom") });
    renderWidget();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Okunamadı: Özellik izleme.");
    expect(alert).toHaveTextContent("Backend HTTP 500 ile yanıt verdi.");
    expect(alert).toHaveTextContent("Üst çubuktaki Yenile'yi kullanın");
    expect(alert).not.toHaveTextContent("boom");
    expect(drill()).toBeInTheDocument();
  });

  it("names the read that failed", async () => {
    world({ series: new ApiError(502, "x") });
    renderWidget();
    expect(await screen.findByText(/Okunamadı: Seri\./)).toBeInTheDocument();
    world({ values: new ApiError(500, "x") });
  });

  it("says the latest refresh failed and keeps the previous answer", async () => {
    world();
    const { rerender } = renderWidget();
    await screen.findByRole("img");
    vi.spyOn(api, "propertyWatchBuckets").mockRejectedValue(new ApiError(503, "x"));
    shell.tick = 1;
    rerender(<PropertyWatchWidget id="w_pw1" config={{ watch_id: WATCH_ID }} range={null} expanded={false} />);
    expect(await screen.findByText(/Son yenileme başarısız oldu\./)).toBeInTheDocument();
    expect(screen.getByRole("img")).toBeInTheDocument();
  });

  it("says forbidden with the permission and who grants it", async () => {
    world({ watch: new ApiError(403, "no") });
    renderWidget();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Rolünüz özellik izlemelerini okuyamaz (izin: read:property_watches)");
    expect(alert).toHaveTextContent("Bu izni bir yönetici verir.");
  });

  it("says the chosen watch was deleted", async () => {
    world({ watch: new ApiError(404, "gone") });
    renderWidget();
    expect(await screen.findByText("Bu özellik izleme silindi.")).toBeInTheDocument();
  });
});
