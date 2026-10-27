// The log source EPS widget and its chart in Turkish: every state (not chosen, gone, loading,
// failed, forbidden, empty, none collected), the honesty qualifiers, the chart's axis names with
// their unit, the tooltip of each kind of bucket and the Turkish number form.

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { failed, forbidden, ok } from "@/components/home/testkit";
import { createTextTranslator } from "@/i18n/textTranslator";
import { homeReads } from "@/lib/api";
import { behavior } from "@/lib/home/fixtures";
import { fleetTextOf } from "@/lib/fleetText";
import { kindMeaning } from "@/lib/timeseries";
import { WidgetRuntimeContext, type WidgetRuntime } from "@/widgets/runtime";

import tr from "../../../messages/tr";
import { SourceEps } from "./SourceEps";
import { buildSourceEpsOption, sourceEpsModel } from "./SourceEpsChart";
import { SOURCE, epsCell, eps, series } from "./subjectFixtures";
import type { HomePalette } from "@/lib/home/chartPalette";

const NB = " ";
const turkish = fleetTextOf(createTextTranslator("tr", tr), "tr");

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on: vi.fn() })),
  };
});
vi.mock("echarts/core", () => ({ init, use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) =>
    key === null ? { status: "idle" } : (res.byKey[key] ?? { status: "loading" }),
}));

const runtime = (over: Partial<WidgetRuntime> = {}): WidgetRuntime => ({
  rangeOptions: ["1h", "24h", "7d"],
  override: null,
  dashboardRange: "24h",
  setRange: vi.fn(),
  onExpand: null,
  expanded: false,
  onConfigure: null,
  ...over,
});

const LIST = homeReads.sourceBehaviors().key;
const SER = (w: "1h" | "24h" | "7d" = "24h") => homeReads.sourceSeries(SOURCE, w).key;
const CELLS = homeReads.sourceCells(SOURCE).key;
const cfg = { source: SOURCE };

function set(over: Record<string, unknown>) {
  res.byKey = {
    [LIST]: ok([behavior({ log_source_id: SOURCE, name: "fw-edge-01" })]),
    [CELLS]: ok([epsCell()]),
    ...over,
  };
}

beforeEach(() => {
  res.byKey = {};
  setOption.mockClear();
  init.mockClear();
  setTestLocale("tr");
});

describe("chart.eps_observed_expected in Turkish", () => {
  it("not chosen: says so in words, then offers Configure while customizing", () => {
    const a = render(<SourceEps config={{ source: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Bir log source seçin.");
    expect(screen.getByRole("status")).toHaveTextContent("Birini seçmek için Özelleştir'i, sonra Yapılandır'ı açın.");
    expect(screen.queryByRole("img")).toBeNull();
    a.unmount();
    const onConfigure = vi.fn();
    render(
      <WidgetRuntimeContext.Provider value={runtime({ onConfigure })}>
        <SourceEps config={{ source: null }} />
      </WidgetRuntimeContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Yapılandır" }));
    expect(onConfigure).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).not.toHaveTextContent("Özelleştir'i");
  });

  it("gone: a source that is no longer monitored says so and draws no other source", () => {
    set({ [LIST]: ok([behavior({ log_source_id: "someone-else" })]), [SER()]: ok(series([eps(0)])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent("Bu log source artık izlenmiyor.");
    expect(screen.getByRole("status")).toHaveTextContent("Yerine başka bir kaynak çizilmez.");
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Log source EPS değeri");
    expect(screen.getByRole("link", { name: "Kaynaklar" })).toHaveAttribute("href", "/behavior/sources");
    expect(init).not.toHaveBeenCalled();
  });

  it("is loading, failed and forbidden text with the permission", () => {
    const a = render(<SourceEps config={cfg} />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Yükleniyor|yükleniyor…$/);
    a.unmount();
    set({ [SER()]: failed(502) });
    const b = render(<SourceEps config={cfg} />);
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 502");
    expect(screen.getByRole("alert")).toHaveTextContent("kaynağın EPS serisi");
    b.unmount();
    set({ [SER()]: forbidden });
    render(<SourceEps config={cfg} />);
    expect(screen.getAllByRole("status").at(-1)).toHaveTextContent("izin: read:anomalies");
  });

  it("titles the widget with the source and the range in Turkish, and leads to the source page", () => {
    set({ [SER()]: ok(series([eps(0), eps(5)])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(`fw-edge-0124${NB}sa`);
    expect(screen.getByRole("link", { name: "Kaynak" })).toHaveAttribute("href", `/behavior/sources/${SOURCE}?range=24`);
    expect(screen.getByRole("img")).toHaveAccessibleName(`fw-edge-01: EPS değeri ve beklenen aralık, son 24${NB}sa`);
    expect(screen.getByText(/Son 24.sa içinde kayıtlı 2.\/.2 zaman dilimi tam toplandı\./)).toBeInTheDocument();
    const key = screen.getByRole("list", { name: "Grafik anahtarı" });
    expect(key).toHaveTextContent("Gözlenen");
    expect(key).toHaveTextContent("Beklenen medyan");
    expect(key).toHaveTextContent("Beklenen aralık");
    expect(key.querySelector("[title^='Bu kaynağın EPS']")).not.toBeNull();
  });

  it("an empty window is an absence of collection, never zero EPS", () => {
    set({ [SER()]: ok(series([])) });
    render(<SourceEps config={cfg} />);
    expect(document.body).toHaveTextContent(
      "Kayıtlı ölçüm zaman dilimi yok. Bu kaynak için son 24 sa içinde hiçbiri saklanmadı. Bu, toplamanın yokluğudur; sıfır EPS değildir.",
    );
    expect(init).not.toHaveBeenCalled();
  });

  it("no bucket fully collected draws no line", () => {
    set({ [SER()]: ok(series([eps(0, { completeness: "MISSING" })])) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByText("Hiçbir zaman dilimi tam toplanmadı.")).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("INSUFFICIENT_DATA: says why there is no band, in the badge and in the note", () => {
    set({ [SER()]: ok(series([eps(0), eps(5)])), [CELLS]: ok([epsCell({ is_reliable: false })]) });
    render(<SourceEps config={cfg} />);
    expect(screen.getAllByText("Yetersiz veri").length).toBeGreaterThan(0);
    expect(screen.getByText(/Bu pencere için henüz güvenilir bir beklenen aralık yok/)).toBeInTheDocument();
  });

  it("says when the expected range could not be read at all", () => {
    set({ [SER()]: ok(series([eps(0)])), [CELLS]: failed(500) });
    render(<SourceEps config={cfg} />);
    expect(screen.getByText("Beklenen aralık okunamadı; bu yüzden çizilmedi.")).toBeInTheDocument();
  });

  it("PARTIAL and TRUNCATED: the badge counts in the Turkish form", () => {
    set({ [SER()]: ok(series([eps(0), eps(5, { completeness: "PARTIAL" }), eps(10)])) });
    const a = render(<SourceEps config={cfg} />);
    expect(document.body.textContent).toContain(`Kısmi: 2${NB}/${NB}3 kayıtlı zaman dilimi toplandı`);
    a.unmount();
    set({ [SER()]: ok(series([eps(0)], { total: 5000, truncated: true })) });
    render(<SourceEps config={cfg} />);
    expect(document.body.textContent).toContain(`Kırpılmış: ilk 1${NB}/${NB}5.000`);
  });

  it("expanded, leads with the source and says what the figure is", () => {
    set({ [SER()]: ok(series([eps(0)])) });
    render(
      <WidgetRuntimeContext.Provider value={runtime({ expanded: true })}>
        <SourceEps config={cfg} />
      </WidgetRuntimeContext.Provider>,
    );
    expect(document.body).toHaveTextContent("fw-edge-01: saniyedeki olay sayısı, son 24 sa.");
  });
});

describe("the EPS chart in Turkish", () => {
  const palette = new Proxy({}, { get: () => "#000" }) as unknown as HomePalette;
  const buckets = [
    eps(0, { average_eps: 1234.5 }),
    eps(5, { completeness: "PARTIAL", average_eps: 12.25 }),
    eps(10, { completeness: "MISSING", average_eps: 0 }),
  ];
  const lookup = (t: number) => (t === Date.parse(buckets[0].bucket_start) ? { median: 1000, low: 800, high: 1500 } : null);

  function tip(words: Parameters<typeof buildSourceEpsOption>[2], i: number) {
    const option = buildSourceEpsOption(sourceEpsModel(buckets, lookup), palette, words) as {
      tooltip: { formatter: (p: unknown) => string };
      xAxis: { name: string };
      yAxis: { name: string };
    };
    return { text: option.tooltip.formatter({ dataIndex: i }), option };
  }

  it("names the axes in Turkish with the unit", () => {
    const { option } = tip(turkish, 0);
    expect(option.xAxis.name).toBe("Zaman (UTC)");
    expect(option.yAxis.name).toBe("Saniyedeki olay sayısı (EPS)");
  });

  it("writes a measured bucket with its median and band in the Turkish form and unit", () => {
    const { text } = tip(turkish, 0);
    expect(text).toContain(`Gözlenen: 1.234,50${NB}EPS`);
    expect(text).toContain(`Beklenen medyan: 1.000,00${NB}EPS`);
    expect(text).toContain(`Beklenen aralık: 800,00 – 1.500,00${NB}EPS`);
    expect(text).toContain("Toplama: aralığın tamamı gözlendi");
  });

  it("says a partial bucket is not a measurement, and what the partial collection reported", () => {
    const { text } = tip(turkish, 1);
    expect(text).toContain("Gözlenen: ölçülmedi (kısmen toplandı — bildirilen hacim bir ölçüm değil, eksik sayımdır)");
    expect(text).toContain(`Kısmi toplamanın bildirdiği: 12,25${NB}EPS`);
    expect(text).toContain("Beklenen aralık: bu hafta günü ve saat için güvenilir baseline yok");
    expect(text).not.toContain("Beklenen medyan");
  });

  it("says a missing bucket is not evidence of silence, never a zero", () => {
    const { text } = tip(turkish, 2);
    expect(text).toContain("Gözlenen: ölçülmedi (aralık toplanmadı — bu, sessizliğin kanıtı değildir)");
    expect(text).not.toContain("Gözlenen: 0");
  });

  it("keeps the English tooltip, with its toFixed figures, by default", () => {
    const option = buildSourceEpsOption(sourceEpsModel(buckets, lookup), palette) as {
      tooltip: { formatter: (p: unknown) => string };
    };
    const text = option.tooltip.formatter({ dataIndex: 0 });
    expect(text).toContain("Observed: 1234.50 EPS");
    expect(text).toContain("Expected range: 800.00 – 1500.00 EPS");
    expect(text).toContain("Collection: whole interval observed");
  });

  it("has a Turkish meaning for every collection kind, and the English is the timeseries' own", () => {
    for (const kind of ["COMPLETE", "PARTIAL", "MISSING", "GAP"] as const) {
      expect(kindMeaning(kind, { t: turkish.say, locale: "tr" })).not.toBe(kindMeaning(kind));
    }
  });

  it("groups the y axis in the Turkish form and leaves the English axis to ECharts", () => {
    const axis = (words?: Parameters<typeof buildSourceEpsOption>[2]) =>
      (buildSourceEpsOption(sourceEpsModel(buckets, lookup), palette, words) as {
        yAxis: { axisLabel: { formatter?: (v: number) => string } };
      }).yAxis.axisLabel;
    expect(axis(turkish).formatter?.(2000)).toBe("2.000");
    expect(axis(turkish).formatter?.(1500000)).toBe("1.500.000");
    expect(axis(turkish).formatter?.(300)).toBe("300");
    expect(axis().formatter).toBeUndefined();
  });

  it("words the history span label and the origin line in Turkish", () => {
    const history = [eps(0, { origin: "HISTORY" } as never), eps(5)];
    const option = buildSourceEpsOption(sourceEpsModel(history, null, true), palette, turkish) as {
      series: Array<{ markArea?: { data: Array<[{ name?: string }]> } }>;
      tooltip: { formatter: (p: unknown) => string };
    };
    expect(option.series[0].markArea?.data[0][0].name).toBe("QRadar geçmişinden okundu, değerlendirilmedi");
    expect(option.tooltip.formatter({ dataIndex: 0 })).toContain("QRadar geçmişinden okundu, değerlendirilmedi");
  });

  it("words the rendered history key and text alternative in Turkish", () => {
    set({
      [SER()]: ok(
        series([eps(0, { origin: "HISTORY" } as never), eps(5)]),
      ),
    });
    render(<SourceEps config={cfg} />);
    expect(screen.getByRole("list", { name: "Grafik anahtarı" })).toHaveTextContent(
      "QRadar geçmişinden okundu, değerlendirilmedi",
    );
    expect(screen.getByText(/1.\/.2 zaman dilimi QRadar geçmişinden okundu/)).toBeInTheDocument();
  });
});
