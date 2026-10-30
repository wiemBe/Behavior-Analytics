// ResultTrend and the property-watch chart in Turkish (D-37), and in English with no translator:
// the search page and the watch pages pass none and keep today's text.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createLocaleTranslator } from "@/i18n/testTranslator";
import { ApiError, api, type SearchExecution } from "@/lib/api";
import { formatRunDuration } from "@/lib/searchRuns";
import { runReadout } from "@/components/searches/authoring";
import { buildPwOption, pwTooltip, type PwPalette } from "@/components/propertywatch/pwChartOption";
import { buildPlot } from "@/components/propertywatch/pwSeries";
import { bucket, iso, point, series } from "@/components/propertywatch/fixtures";
import { ENGLISH_PW, pwUnit, type PwI18n } from "@/components/propertywatch/pwText";
import { PropertyWatchSeriesChart } from "@/components/propertywatch/PropertyWatchSeriesChart";
import { ConfigNotice } from "@/components/catalog/ConfigNotice";

import { setTestLocale } from "../../vitest.setup";

import { ResultTrend } from "./ResultTrend";
import type { TrendI18n } from "./trendText";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })) };
});
vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use: vi.fn() };
});
vi.mock("echarts/charts", () => ({ LineChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const tr: TrendI18n = { t: createLocaleTranslator("tr"), locale: "tr" };
const pwTr: PwI18n = { t: createLocaleTranslator("tr"), locale: "tr" };

const emptyGroups = {
  search_id: "s-1", execution_id: null, started_at: null, status: null,
  truncated: false, values_are_lower_bounds: false, empty: "no_execution" as const, total: 0, rows: [],
};

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", TestResizeObserver);
  init.mockClear();
  setOption.mockClear();
  vi.spyOn(api, "searchMetricKeys").mockResolvedValue({
    search_id: "s-1", total: 0, count: 0, keys: [],
  });
  vi.spyOn(api, "searchLatestGroups").mockResolvedValue(emptyGroups);
});

describe("ResultTrend states in Turkish", () => {
  it("words the never-run line, with the creation time, for the page and the widget", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue({
      search_id: "s-1", metric_key: "total", threshold_value: null, threshold_operator: "GT",
      count: 0, points: [],
    });
    const { unmount } = render(
      <ResultTrend searchId="s-1" executions={[]} createdAt="2026-10-01T00:00:00Z" i18n={tr} />,
    );
    expect(await screen.findByText(/hiç çalışmadı/)).toHaveTextContent(
      "Bu arama, 2026-10-01 00:00:00 UTC tarihinde oluşturulduğundan beri hiç çalışmadı. Bu sayfadan Şimdi çalıştır ile çalıştırın veya zamanlamasını bekleyin.",
    );
    unmount();
    render(
      <ResultTrend searchId="s-1" executions={[]} createdAt={null} variant="widget" i18n={tr} />,
    );
    expect(await screen.findByText(/Oluşturulma zamanı kaydedilmemiş/)).toHaveTextContent(
      "Bu arama hiç çalışmadı. Oluşturulma zamanı kaydedilmemiş. Arama sayfasından Şimdi çalıştır",
    );
  });

  it("words the refusal and the failure, with the permission named", async () => {
    vi.spyOn(api, "searchResults").mockRejectedValue(new ApiError(403, "no"));
    const { unmount } = render(<ResultTrend searchId="s-1" i18n={tr} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Rolünüz arama sonuçlarını okuyamıyor (izin: read:searches).",
    );
    unmount();
    vi.spyOn(api, "searchResults").mockRejectedValue(new ApiError(500, "x"));
    render(<ResultTrend searchId="s-1" i18n={tr} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sunucu isteği tamamlayamadı. Kısa süre sonra yeniden deneyin.",
    );
  });

  it("words the loading state and the stale line", async () => {
    vi.spyOn(api, "searchResults").mockResolvedValue({
      search_id: "s-1", metric_key: "total", threshold_value: null, threshold_operator: "GT",
      count: 1,
      points: [
        {
          bucket_start: "2026-07-20T10:00:00Z", metric_key: "total", value: 1234, dimensions: {},
          execution_id: "e-1", execution_status: "COMPLETED", duration_ms: 90500, result_count: 1,
          threshold_breached: false, query_version: 1, query_version_id: "v-1",
        },
      ],
    });
    render(
      <ResultTrend
        searchId="s-1"
        i18n={tr}
        search={{ last_run_at: "2026-07-20T10:00:00Z", next_run_at: "2026-07-20T11:00:00Z", consecutive_failures: 0 }}
        executions={[{ id: "e-1", status: "COMPLETED", completed_at: "2026-07-20T10:00:00Z", started_at: "2026-07-20T10:00:00Z" } as SearchExecution]}
        now={Date.parse("2026-07-20T15:00:00Z")}
      />,
    );
    expect(screen.getByText("Sonuç eğilimi yükleniyor…")).toBeInTheDocument();
    expect(await screen.findByText(/Güncel değil: son başarılı çalıştırma/)).toHaveTextContent(
      "2026-07-20 10:00:00 UTC zamanındaydı; üzerinden 5 sa geçti (zamanlama aralığı 1 sa).",
    );
    expect(screen.getByText(/1 nokta · metrik/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Arama sonucu eğilimi" })).toBeInTheDocument();
  });

  it("keeps English with no translator", async () => {
    vi.spyOn(api, "searchResults").mockRejectedValue(new ApiError(500, "x"));
    render(<ResultTrend searchId="s-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server could not complete the request. Try again shortly.",
    );
  });
});

describe("shared helpers take a translator and default to English", () => {
  it("formats a run duration with the units of the language", () => {
    const units = (key: string, v?: Record<string, string | number>) => tr.t(`units.${key}`, v);
    expect(formatRunDuration(41250, units as never, "tr")).toBe("41,3\u00a0sn");
    expect(formatRunDuration(300000, units as never, "tr")).toBe("5\u00a0dk 0\u00a0sn");
    expect(formatRunDuration(41250)).toBe("41.3 s");
    expect(formatRunDuration(300000)).toBe("5 min 0 s");
  });

  it("words a failed run by its error code, and keeps English by default", () => {
    const failed = {
      status: "FAILED", error_type: "TIMEOUT", truncated: false, threshold_breached: false,
      result_count: null,
    } as unknown as SearchExecution;
    expect(runReadout(failed, 2, tr.t).text).toContain("Son çalıştırma başarısız oldu. Hata türü Zaman aşımı. Arama zaman aşımını aştı.");
    expect(runReadout(failed, 2).text).toContain("The latest run failed. Error type TIMEOUT. The search exceeded its timeout.");
    const unknown = { ...failed, error_type: "NEW_KIND" } as SearchExecution;
    expect(runReadout(unknown, null).text).toContain("Error type NEW_KIND. Inspect");
  });
});

describe("the property-watch chart in Turkish", () => {
  const palette: PwPalette = {
    cat: ["#1", "#2", "#3", "#4", "#5"], grid: "#g", axis: "#a", label: "#l", band: "#b", gap: "#gap",
    anomaly: "#an", history: "#h", marker: "#m", expected: "#e", tooltipBg: "#tb",
    tooltipBorder: "#tbd", tooltipText: "#tt", font: "Inter",
  };
  const model = buildPlot({
    series: [series("a", "svc-backup", [point(0), point(1)], true)],
    windowStart: iso(0), windowEnd: iso(3), bucketSeconds: 300,
  });

  it("words the unit, the axes and the tooltip", () => {
    expect(pwUnit(pwTr, 300)).toBe("olay\u00a0/\u00a05\u00a0dk");
    expect(pwUnit(pwTr, 90)).toBe("olay\u00a0/\u00a090\u00a0sn");
    expect(pwUnit(pwTr, null)).toBe("olay / zaman dilimi");
    expect(pwUnit(ENGLISH_PW, 300)).toBe("events per 5 min");
    const option = buildPwOption({ model, palette, i18n: pwTr }) as unknown as {
      xAxis: Array<{ name: string }>;
      yAxis: Array<{ name: string }>;
    };
    expect(option.xAxis[0].name).toBe("Zaman (UTC)");
    expect(option.yAxis[0].name).toBe("olay\u00a0/\u00a05\u00a0dk");
    const tip = pwTooltip(model, 0, { unit: pwUnit(pwTr, 300), i18n: pwTr });
    expect(tip).toContain("svc-backup: 100 olay\u00a0/\u00a05\u00a0dk (kesin sayı)");
    expect(tip).toContain("Beklenen medyan: 100,0 olay\u00a0/\u00a05\u00a0dk");
    expect(pwTooltip(model, 0, { unit: "events per 5 min" })).toContain(
      "svc-backup: 100 events per 5 min (exact count)",
    );
  });

  it("words the legend, the pinned mark, the gap key and the notes, and gives ECharts the Turkish months", () => {
    const list = [series("a", "svc-backup", [point(0), point(2)], true)];
    render(
      <PropertyWatchSeriesChart
        series={list}
        windowStart={iso(0)}
        windowEnd={iso(3)}
        bucketSeconds={300}
        collected={[iso(0), iso(2)]}
        i18n={pwTr}
      />,
    );
    expect(screen.getByRole("list", { name: "Grafik anahtarı" })).toHaveTextContent("sabitlenmiş");
    expect(screen.getByText("Toplanmadı")).toBeInTheDocument();
    expect(screen.getByText(/Çizgideki boşluklar toplanmamış zaman dilimleridir/)).toBeInTheDocument();
    const initArgs = init.mock.calls.at(-1) as unknown as [unknown, unknown, { locale: { time: { monthAbbr: string[] } } }];
    expect(initArgs[2].locale.time.monthAbbr[9]).toBe("Eki");
    expect(bucket).toBeDefined();
  });

  it("keeps the English legend with no translator", () => {
    render(
      <PropertyWatchSeriesChart
        series={[series("a", "svc-backup", [point(0), point(2)], true)]}
        windowStart={iso(0)}
        windowEnd={iso(3)}
        bucketSeconds={300}
        collected={[iso(0), iso(2)]}
      />,
    );
    expect(screen.getByRole("list", { name: "Chart key" })).toHaveTextContent("pinned");
    expect(screen.getByText("Not collected")).toBeInTheDocument();
  });
});

describe("ConfigNotice in Turkish", () => {
  it("words the Configure button", async () => {
    setTestLocale("tr");
    const { WidgetRuntimeContext } = await import("@/widgets/runtime");
    render(
      <WidgetRuntimeContext.Provider
        value={{ rangeOptions: null, override: null, dashboardRange: "24h", setRange: () => undefined, onExpand: null, expanded: false, onConfigure: () => undefined }}
      >
        <ConfigNotice lead="a" text="b" hint="c" />
      </WidgetRuntimeContext.Provider>,
    );
    expect(screen.getByRole("button", { name: "Yapılandır" })).toBeInTheDocument();
  });
});
