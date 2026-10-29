// Turkish rendering of the Home widgets (D-37): every state a widget has, the honesty states,
// stale and forbidden, the Turkish number form, the enum labels, and the chart formatters.
// English stays the default of the harness, so each test opts in with setTestLocale("tr").
// Values the backend or `lib/*` write stay English and are asserted as such where it matters.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads, type SocOverview } from "@/lib/api";
import {
  analytics,
  anomaly,
  behavior,
  epsPoint,
  epsSeries,
  fleet,
  fleetClass,
  offenseRow,
  page,
  summary,
} from "@/lib/home/fixtures";
import { lastHours } from "@/lib/home/severityPerHour";
import { setTestLocale } from "../../../vitest.setup";

import { EpsByClass } from "./EpsByClass";
import { EpsChart } from "./EpsChart";
import { KpiAnomalies } from "./KpiAnomalies";
import { KpiLogSources } from "./KpiLogSources";
import { KpiOffenses } from "./KpiOffenses";
import { KpiTotalEps } from "./KpiTotalEps";
import { LogSourceHealth } from "./LogSourceHealth";
import { RecentAnomalies } from "./RecentAnomalies";
import { RecentOffenses } from "./RecentOffenses";
import { SeverityChart } from "./SeverityChart";
import { TopSources } from "./TopSources";
import { ENGLISH_CHART, epsTooltip, severityTooltip } from "./chartOptions";
import { epsChartModel } from "@/lib/home/epsChart";
import { severityPerHour } from "@/lib/home/severityPerHour";
import { failed, forbidden, loading, ok } from "./testkit";

const { setOption, init, use, on } = vi.hoisted(() => {
  const setOption = vi.fn();
  const on = vi.fn();
  return {
    setOption,
    on,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on })),
    use: vi.fn(),
  };
});
vi.mock("echarts/core", () => ({ init, use }));
vi.mock("echarts/charts", () => ({ LineChart: {}, PieChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const SUMMARY = homeReads.fleetSummary("24h").key;
const SERIES = homeReads.epsSeries("24h", "none").key;
const ANALYTICS = homeReads.offenseAnalytics().key;
const PROBE = homeReads.offenseProbe().key;
const ANOM_SUM = homeReads.anomalySummary().key;
const SOURCES = homeReads.sourceBehaviors().key;
const OVERVIEW = homeReads.overview().key;
const ACTIVE = homeReads.activeAnomalies(5).key;
const OPEN_OFFENSES = homeReads.recentOffenses(5).key;
const WAF = homeReads.activeWafAnomalies(5).key;

const overview = { log_sources: { total_log_sources: 1500, in_maintenance: 2 } } as SocOverview;
const NBSP = " ";

beforeEach(() => {
  setTestLocale("tr");
  res.byKey = {};
  setOption.mockClear();
  init.mockClear();
  on.mockClear();
});

describe("KpiTotalEps in Turkish", () => {
  it("writes the figure, the unit, the window and the class share the Turkish way", () => {
    res.byKey[SUMMARY] = ok(
      fleet([fleetClass("FIREWALL", { mean_eps: 800 }), fleetClass("WAF", { mean_eps: 200 })]),
    );
    res.byKey[SERIES] = ok(epsSeries([epsPoint(0, 1), epsPoint(1, 2)], { window: "24h" }));
    render(<KpiTotalEps />);
    expect(screen.getByRole("heading", { name: "Toplam EPS, 24\u00a0sa" })).toBeInTheDocument();
    expect(screen.getByText("1.000,00")).toBeInTheDocument();
    expect(screen.getByText("EPS")).toBeInTheDocument();
    expect(screen.getByTitle("24 sa boyunca ortalama, yalnızca tamamlanmış aralıklar.")).toBeInTheDocument();
    expect(screen.getByText(`en büyük: Güvenlik duvarı %80`)).toBeInTheDocument();
    expect(screen.getByTitle("Güvenlik duvarı, toplamın 800,00 EPS değeri")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Filoyu sınıfa göre görüntüle" })).toHaveAttribute(
      "href",
      "/fleet?window=24h",
    );
    expect(screen.getByText(/beklenen — \(güvenilir baseline yok\)/)).toBeInTheDocument();
  });

  it("says loading, forbidden and failed in Turkish, with the permission and the status", () => {
    res.byKey[SUMMARY] = loading;
    const { unmount } = render(<KpiTotalEps />);
    expect(screen.getByRole("status")).toHaveTextContent("Filo EPS yükleniyor…");
    unmount();
    res.byKey[SUMMARY] = forbidden;
    const second = render(<KpiTotalEps />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Rolünüz bunu okuyamıyor: filo EPS (izin: read:fleet). Hiçbir şey değiştirilmedi.",
    );
    second.unmount();
    res.byKey[SUMMARY] = failed(502);
    render(<KpiTotalEps />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Okunamadı: filo EPS.");
    expect(alert).toHaveTextContent("Backend HTTP 502 ile yanıt verdi.");
    expect(alert).toHaveTextContent("Hiçbir şey değiştirilmedi.");
  });

  it("says INSUFFICIENT_DATA with the Turkish reason, never a zero", () => {
    res.byKey[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: null })]));
    render(<KpiTotalEps />);
    expect(screen.getByText("Bu aralıkta tamamlanmış zaman dilimi olan sınıf yok.")).toBeInTheDocument();
    expect(screen.queryByText(/^0/)).toBeNull();
  });

  it("names the trend's own failures in Turkish and leaves the card standing", () => {
    res.byKey[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: 800 })]));
    res.byKey[SERIES] = forbidden;
    render(<KpiTotalEps />);
    expect(
      screen.getByText("Eğilim gösterilmiyor: rolünüz bunu okuyamıyor (izin: read:fleet)."),
    ).toBeInTheDocument();
    expect(screen.getByText("800,00")).toBeInTheDocument();
  });
});

describe("KpiLogSources in Turkish", () => {
  it("writes the counts, the judged line and the silent noun in Turkish", () => {
    res.byKey[ANOM_SUM] = ok(summary({ monitored_sources: 3, silent_sources: 1 }));
    res.byKey[SOURCES] = ok([
      behavior({ log_source_id: "a", state: "NORMAL" }),
      behavior({ log_source_id: "b", state: "OPEN" }),
      behavior({ log_source_id: "c", state: "NORMAL" }),
    ]);
    res.byKey[OVERVIEW] = ok(overview);
    render(<KpiLogSources />);
    expect(screen.getByRole("heading", { name: "Log source'lar, güncel durum" })).toBeInTheDocument();
    expect(screen.getByText("/ 1.500 izlenen kaynak")).toBeInTheDocument();
    expect(screen.getByText(/Değerlendirilen 3 kaynak: 2 normal/)).toBeInTheDocument();
    expect(screen.getByText("1 sessiz", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Log source'ları görüntüle" })).toBeInTheDocument();
  });

  it("explains an empty fleet, and a refused total, in Turkish", () => {
    res.byKey[ANOM_SUM] = ok(summary({ monitored_sources: 0 }));
    res.byKey[SOURCES] = ok([]);
    res.byKey[OVERVIEW] = forbidden;
    render(<KpiLogSources />);
    expect(screen.getByText(/İzlenen log source yok\. Hacim baseline'ını/)).toBeInTheDocument();
    expect(
      screen.getByText("Toplam log source sayısı gösterilmiyor: rolünüz bunu okuyamıyor (izin: read:overview)."),
    ).toBeInTheDocument();
  });

  it("says that no source is judged yet as INSUFFICIENT_DATA", () => {
    res.byKey[ANOM_SUM] = ok(summary({ monitored_sources: 1 }));
    res.byKey[SOURCES] = ok([behavior({ state: "INSUFFICIENT_DATA" })]);
    res.byKey[OVERVIEW] = failed(500);
    render(<KpiLogSources />);
    // The pill and the health legend both say it.
    expect(screen.getAllByText("Yetersiz veri")).toHaveLength(2);
    expect(screen.getByText("İzlenen hiçbir kaynağın henüz bir kararı yok.")).toBeInTheDocument();
    expect(screen.getByText("Toplam log source sayısı kullanılamıyor.")).toBeInTheDocument();
  });
});

describe("KpiOffenses in Turkish", () => {
  it("writes the split with Turkish nouns and numbers", () => {
    res.byKey[ANALYTICS] = ok(
      analytics({ active: 1204, critical: 3, unassigned: 5, exceeding_sla: 2 }, [
        { key: "8", count: 3 },
        { key: "4", count: 1200 },
        { key: "unassigned", count: 1 },
      ]),
    );
    res.byKey[PROBE] = ok(page([offenseRow()], 40));
    render(<KpiOffenses />);
    expect(screen.getByRole("heading", { name: "Açık offense'lar, son kesit" })).toBeInTheDocument();
    expect(screen.getByText("1.204")).toBeInTheDocument();
    expect(screen.getByText("açık offense")).toBeInTheDocument();
    const lines = screen.getAllByText((_, node) => node?.classList.contains("kpi-line") ?? false);
    expect(lines[0].textContent?.replaceAll(NBSP, " ")).toBe("3 kritik (≥ 7) · 1.200 altında");
    expect(lines[1].textContent).toBe(`+${NBSP}1${NBSP}büyüklüğü bilinmeyen`);
    expect(lines[2].textContent?.replaceAll(NBSP, " ")).toBe("5 atanmamış · 2 SLA aşımı");
    expect(screen.getByTitle(/Kritik, büyüklüğü 7 veya üzeri/)).toBeInTheDocument();
  });

  it("says NOT_COLLECTED, with the Turkish reason, when no snapshot exists", () => {
    res.byKey[ANALYTICS] = ok(analytics({ active: null as unknown as number }));
    res.byKey[PROBE] = ok(page([], 0));
    render(<KpiOffenses />);
    expect(screen.getByText("Toplanmadı")).toBeInTheDocument();
    expect(screen.getByText("Henüz offense kesiti toplanmadı.")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("says forbidden with the permission", () => {
    res.byKey[ANALYTICS] = forbidden;
    render(<KpiOffenses />);
    expect(screen.getByRole("status")).toHaveTextContent("Rolünüz bunu okuyamıyor: offense'lar (izin: read:offenses)");
  });
});

describe("KpiAnomalies in Turkish", () => {
  it("labels the detector and lifecycle partitions by key", () => {
    res.byKey[ANOM_SUM] = ok(
      summary({ open_anomalies: 12, spikes: 5, drops: 3, silent_sources: 2, candidates: 4, recovering: 1, insufficient_data_sources: 1 }),
    );
    render(<KpiAnomalies />);
    expect(screen.getByRole("heading", { name: "Etkin anomaliler, güncel durum" })).toBeInTheDocument();
    expect(screen.getByText("etkin anomali")).toBeInTheDocument();
    const detector = screen.getByText("Algılayıcıya göre:").closest("p")!;
    expect(detector.textContent?.replaceAll(NBSP, " ")).toBe("Algılayıcıya göre: 5 ani artış · 3 düşüş · 2 sessiz · 2 diğer");
    const lifecycle = screen.getByText("Yaşam döngüsü:").closest("p")!;
    expect(lifecycle.textContent?.replaceAll(NBSP, " ")).toBe("Yaşam döngüsü: 7 açık · 4 aday · 1 düzeliyor");
    expect(screen.getByText("1 kaynak değerlendirilmedi; bu sayımlara dahil değil.")).toBeInTheDocument();
  });

  it("says INSUFFICIENT_DATA when nothing is monitored", () => {
    res.byKey[ANOM_SUM] = ok(summary({ monitored_sources: 0, open_anomalies: 0, insufficient_data_sources: 0 }));
    render(<KpiAnomalies />);
    expect(screen.getByText("İzlenen log source yok; henüz değerlendirilecek bir şey yok.")).toBeInTheDocument();
    expect(screen.getByText("Yetersiz veri")).toBeInTheDocument();
  });
});

describe("EpsChart in Turkish", () => {
  const pts = (...eps: Array<number | null>) => eps.map((e, i) => epsPoint(i, e, e === null ? null : 4));

  it("describes the chart, its key and its gaps in Turkish, with the PARTIAL badge", () => {
    res.byKey[homeReads.epsSeries("24h", "none").key] = ok(epsSeries(pts(5, null, 6, 7), { window: "24h" }));
    render(<EpsChart />);
    expect(screen.getByRole("heading", { name: /Filo EPS/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Filo EPS, 24\u00a0sa" })).toBeInTheDocument();
    const key = screen.getByRole("list", { name: "Grafik göstergesi" });
    expect(within(key).getByText("Gözlenen")).toBeInTheDocument();
    expect(within(key).getByText("Kaynak başına beklenenlerin toplamı")).toBeInTheDocument();
    expect(within(key).getByText("Ölçülmedi")).toBeInTheDocument();
    expect(screen.getByText(/Kısmi: 3 \/ 4 adım toplandı/)).toBeInTheDocument();
    expect(
      screen.getByText(/3 \/ 4 adım ölçüldü, en yüksek 7,00 EPS/, { selector: ".sr-only" }),
    ).toBeInTheDocument();
    const option = setOption.mock.calls[0][0] as {
      series: Array<{ name: string }>;
      yAxis: { name: string };
    };
    expect(option.series.map((s) => s.name)).toEqual(["Gözlenen EPS", "Kaynak başına beklenenlerin toplamı"]);
    expect(option.yAxis.name).toBe("EPS");
  });

  it("says NOT_COLLECTED in Turkish when no step was measured, and draws no chart", () => {
    res.byKey[homeReads.epsSeries("24h", "none").key] = ok(epsSeries(pts(null, null), { window: "24h" }));
    render(<EpsChart />);
    expect(screen.getByText(/Toplanmadı/)).toBeInTheDocument();
    expect(screen.getByText(/2 adımın hiçbirinde scope'taki tüm kaynak aralıkları tamamlanmış değil\./)).toBeInTheDocument();
    expect(screen.getByText(/Çizgi çizilmedi; ölçülmeyen bir adım sıfır trafik demek değildir\./)).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });

  it("formats the tooltip and the axis with Turkish numbers, and an unmeasured step with its gap", () => {
    res.byKey[homeReads.epsSeries("24h", "none").key] = ok(epsSeries(pts(1234.5, null), { window: "24h" }));
    render(<EpsChart />);
    const option = setOption.mock.calls[0][0] as {
      tooltip: { formatter: (p: unknown) => string };
      yAxis: { axisLabel: { formatter: (v: number) => string } };
    };
    expect(option.tooltip.formatter([{ dataIndex: 0 }])).toContain("Gözlenen: 1.234,50\u00a0EPS");
    expect(option.tooltip.formatter([{ dataIndex: 1 }])).toContain(
      "Gözlenen: ölçülmedi (3\u00a0/\u00a04 kaynak aralığı ölçüldü)",
    );
    expect(option.yAxis.axisLabel.formatter(12345)).toBe("12.345");
    // The same builders default to English when no language is passed.
    const model = epsChartModel(epsSeries(pts(1234.5)));
    expect(epsTooltip(model, 0, undefined, ENGLISH_CHART)).toContain("Observed: 1,234.50 EPS");
  });

  it("says forbidden and failed in Turkish", () => {
    res.byKey[homeReads.epsSeries("24h", "none").key] = forbidden;
    const view = render(<EpsChart />);
    expect(screen.getByRole("status")).toHaveTextContent("Rolünüz bunu okuyamıyor: filo EPS serisi (izin: read:fleet).");
    view.unmount();
    res.byKey[homeReads.epsSeries("24h", "none").key] = failed(null);
    render(<EpsChart />);
    expect(screen.getByRole("alert")).toHaveTextContent("Okunamadı: filo EPS serisi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Backend yanıt vermedi.");
  });
});

describe("EpsByClass in Turkish", () => {
  it("lists the classes by their Turkish names with Turkish numbers and shares", () => {
    res.byKey[SUMMARY] = ok(
      fleet([fleetClass("FIREWALL", { mean_eps: 800 }), fleetClass("NETWORK", { mean_eps: 200 })]),
    );
    render(<EpsByClass />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("Sınıf")).toBeInTheDocument();
    expect(within(table).getByText("Pay")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Güvenlik duvarı" })).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Ağ" })).toBeInTheDocument();
    expect(within(table).getByText("800,00")).toBeInTheDocument();
    expect(within(table).getByText("%80")).toBeInTheDocument();
    expect(screen.getByText(/Kaynak sınıfına göre ortalama EPS/, { selector: "caption" })).toBeInTheDocument();
    // The tooltip of the donut names the class in Turkish too.
    const option = setOption.mock.calls[0][0] as {
      tooltip: { formatter: (p: unknown) => string };
      series: Array<{ data: Array<{ name: string }> }>;
    };
    expect(option.series[0].data.map((d) => d.name)).toEqual(["Güvenlik duvarı", "Ağ"]);
    expect(option.tooltip.formatter({ name: "Ağ", value: 200, percent: 20 })).toBe("Ağ: 200,00\u00a0EPS (%20)");
  });

  it("names the folded classes and counts the classes with no data, in Turkish", () => {
    const classes = ["FIREWALL", "NETWORK", "DNS", "EMAIL", "CLOUD", "VPN"].map((c, i) =>
      fleetClass(c as never, { mean_eps: 100 - i }),
    );
    res.byKey[SUMMARY] = ok(fleet([...classes]));
    render(<EpsByClass />);
    expect(screen.getByText("Diğer sınıflar")).toBeInTheDocument();
    expect(screen.getByText("Bulut, VPN")).toBeInTheDocument();
  });

  it("says INSUFFICIENT_DATA in Turkish when no class has a mean", () => {
    res.byKey[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { mean_eps: null })]));
    render(<EpsByClass />);
    expect(screen.getByText("Yetersiz veri")).toBeInTheDocument();
    expect(screen.getByText("Bu aralıkta tamamlanmış zaman dilimi olan sınıf yok.")).toBeInTheDocument();
    expect(init).not.toHaveBeenCalled();
  });
});

describe("SeverityChart in Turkish", () => {
  const NOW = Date.parse("2026-10-14T13:20:00Z");
  const range = lastHours(NOW, 24);
  const at = (severity: string, iso = "2026-10-14T09:10:00Z") => anomaly({ severity, detected_at: iso });

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  it("keys the severities in Turkish and words the axis, the chart and the summary", () => {
    res.byKey[homeReads.anomaliesBetween(range.since, range.until, 200).key] = ok(
      page([at("HIGH"), at("HIGH"), at("LOW")]),
    );
    render(<SeverityChart />);
    expect(screen.getByRole("heading", { name: /Önem derecesine göre anomaliler/ })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Önem derecesi göstergesi" })).toHaveTextContent(/Yüksek.*Düşük/);
    expect(screen.getByRole("img", { name: /Saat başına anomaliler, önem derecesine göre/ })).toBeInTheDocument();
    expect(screen.getByText(/24 saatte 3 \(2 yüksek, 1 düşük\)\./, { selector: ".sr-only" })).toBeInTheDocument();
    const option = setOption.mock.calls[0][0] as {
      series: Array<{ name: string }>;
      yAxis: { name: string };
    };
    // The series name stays the code: a click on a column builds its filter from it.
    expect(option.series.map((s) => s.name)).toEqual(["HIGH", "LOW"]);
    expect(option.yAxis.name).toBe("saatte anomali");
  });

  it("says TRUNCATED when the list was capped, and words the tooltip as a lower bound", () => {
    res.byKey[homeReads.anomaliesBetween(range.since, range.until, 200).key] = ok(page([at("HIGH")], 345));
    render(<SeverityChart />);
    expect(screen.getByText(/Kırpılmış: ilk 1 \/ 345/)).toBeInTheDocument();
    const option = setOption.mock.calls[0][0] as {
      yAxis: { name: string };
      tooltip: { formatter: (p: unknown) => string };
    };
    expect(option.yAxis.name).toBe("saatte anomali, en az");
    const hour = option.tooltip.formatter([{ dataIndex: 19 }]);
    expect(hour).toContain("Yüksek: 1");
    expect(hour).toContain("Bu saatte en az 1 (liste kırpıldı)");
    // A capped list with nothing in the window is not a measured zero.
    res.byKey[homeReads.anomaliesBetween(range.since, range.until, 200).key] = ok(
      page([at("HIGH", "2026-09-01T00:00:00Z")], 345),
    );
  });

  it("says an empty window, and a window it cannot count, in Turkish", () => {
    const key = homeReads.anomaliesBetween(range.since, range.until, 200).key;
    res.byKey[key] = ok(page([]));
    const view = render(<SeverityChart />);
    expect(screen.getByText("Son 24 sa içinde açılan anomali yok.")).toBeInTheDocument();
    view.unmount();
    res.byKey[key] = ok(page([at("HIGH", "2026-09-01T00:00:00Z")], 345));
    const second = render(<SeverityChart />);
    expect(screen.getByText(/Liste kırpıldığı için bu, ölçülmüş bir sıfır değildir\./)).toBeInTheDocument();
    second.unmount();
    res.byKey[key] = ok(page([at("HIGH", "2026-09-01T00:00:00Z")], 0));
    render(<SeverityChart />);
    expect(screen.getByText(/Listenin toplamı dönen satırlarla eşleşmiyor/)).toBeInTheDocument();
  });

  it("words the tooltip of a complete list without a lower bound", () => {
    const model = severityPerHour(page([at("HIGH")]), range);
    const turkish = {
      t: ((key: string, values?: Record<string, string | number>) => key + JSON.stringify(values)) as never,
      locale: "tr" as const,
    };
    expect(severityTooltip(model, 19, ENGLISH_CHART)).toContain("1 in this hour");
    expect(severityTooltip(model, 19, turkish)).toContain("home.chart.inHour");
  });
});

describe("TopSources in Turkish", () => {
  const ts = (id: string, events: number, name = id) => ({ id, name, events });

  function setup(behaviors: unknown) {
    res.byKey[SUMMARY] = ok(
      fleet([
        fleetClass("FIREWALL", { top_sources: [ts("a", 86_400_000, "Core Firewall"), ts("b", 43_200_000, "Edge FW")] }),
        fleetClass("PROXY", {
          buckets: { expected: 48, complete: 40, complete_from_history: 0, partial: 4, missing: 4 },
          top_sources: [ts("c", 8_640_000, "Proxy")],
        }),
      ]),
    );
    res.byKey[SOURCES] = behaviors;
  }

  it("writes the headers, classes, states, deviations and EPS the Turkish way", () => {
    setup(
      ok([
        behavior({ log_source_id: "a", name: "Core Firewall", state: "OPEN", deviation_ratio: 3.4 }),
        behavior({ log_source_id: "b", name: "Edge FW", state: "RECOVERING", deviation_ratio: null }),
      ]),
    );
    render(<TopSources />);
    const table = screen.getByRole("table", { name: "EPS'e göre en yoğun log source'lar" });
    for (const header of ["Kaynak", "Ortalama EPS", "Durum"]) {
      expect(within(table).getByText(header)).toBeInTheDocument();
    }
    expect(within(table).getByText(/Sapma/)).toBeInTheDocument();
    expect(within(table).getByText("son zaman dilimi")).toBeInTheDocument();
    expect(within(table).getAllByText("Güvenlik duvarı").length).toBeGreaterThan(0);
    expect(within(table).getByText("1.000,00")).toBeInTheDocument();
    expect(within(table).getByText("3,40x")).toBeInTheDocument();
    expect(within(table).getAllByText("AÇIK").length).toBeGreaterThan(0);
    expect(within(table).getAllByText("DÜZELİYOR").length).toBeGreaterThan(0);
    // The class with missing intervals makes its EPS a floor.
    expect(within(table).getByText("en az")).toBeInTheDocument();
  });

  it("gives the reason a deviation is missing, in Turkish", () => {
    setup(ok([behavior({ log_source_id: "a", name: "Core Firewall", state: "OPEN", deviation_ratio: null, expected_eps: null })]));
    render(<TopSources />);
    expect(screen.getAllByText("Beklenen değer yok").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Davranış listesinde yok").length).toBeGreaterThan(0);
  });

  it("keeps the table when the behavior list is refused, and says why", () => {
    setup(forbidden);
    render(<TopSources />);
    expect(screen.getByText(/Sapma ve durum kullanılamıyor: kaynak davranış listesi okunamıyor \(rolünüz bunu okuyamıyor\)\./)).toBeInTheDocument();
    expect(screen.getAllByText("Durum kullanılamıyor").length).toBeGreaterThan(0);
  });

  it("says loading while the states are read, and the empty case in Turkish", () => {
    setup(loading);
    const view = render(<TopSources />);
    expect(screen.getByRole("status")).toHaveTextContent("Kaynak durumları yükleniyor…");
    view.unmount();
    res.byKey[SUMMARY] = ok(fleet([fleetClass("FIREWALL", { top_sources: [] })]));
    res.byKey[SOURCES] = ok([]);
    render(<TopSources />);
    expect(
      screen.getByText("Son 24 sa içinde tamamlanmış zaman dilimi olan log source yok, bu yüzden EPS'e göre sıralanamıyor."),
    ).toBeInTheDocument();
  });
});

describe("LogSourceHealth in Turkish", () => {
  const sources = [
    behavior({ log_source_id: "a", state: "NORMAL" }),
    behavior({ log_source_id: "b", state: "OPEN" }),
    behavior({ log_source_id: "c", state: "INSUFFICIENT_DATA" }),
  ];

  function setup(over: Record<string, unknown> = {}) {
    res.byKey = {
      [SOURCES]: ok(sources),
      [ANOM_SUM]: ok(summary({ silent_sources: 1, spikes: 4, monitored_sources: 3 })),
      [homeReads.fleetSummary("24h").key]: ok(fleet([], { new_sources: 3, conflict_sources: 0, unclassified_sources: 1500 })),
      [OVERVIEW]: ok(overview),
      ...over,
    };
  }

  it("words the categories, their definitions and their counts in Turkish", () => {
    setup();
    render(<LogSourceHealth />);
    expect(screen.getByText("son durum")).toBeInTheDocument();
    expect(screen.getByText(/Değerlendirilen 2 kaynak: 1 normal/)).toBeInTheDocument();
    expect(screen.getByText(/1 değerlendirilmedi/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Log source sağlığı kategorileri" });
    const row = within(table).getByText("Beklenen hacmi aşan").closest("tr")!;
    expect(row).toHaveTextContent("4");
    expect(within(table).getByText("Sınıflanmamış").closest("tr")).toHaveTextContent("1.500");
    expect(within(table).getByText("Bakımda")).toBeInTheDocument();
    expect(within(table).getByText("Sessiz").closest("td")).toHaveAttribute(
      "title",
      "Algılayıcı, olay beklenen yerde hiç olay görmedi (NO_EVENTS anomalisi).",
    );
  });

  it("leaves a category at a dash with a Turkish reason when its read failed, never zero", () => {
    setup({ [homeReads.fleetSummary("24h").key]: failed(500) });
    render(<LogSourceHealth />);
    const table = screen.getByRole("table", { name: "Log source sağlığı kategorileri" });
    const row = within(table).getByText("Yeni keşfedilen").closest("tr")!;
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(row).toHaveTextContent("Filo özeti kullanılamıyor");
  });

  it("says the summary is unreadable, and the empty fleet, in Turkish", () => {
    setup({ [ANOM_SUM]: forbidden });
    const view = render(<LogSourceHealth />);
    expect(
      screen.getByText(/Kaç kaynağın normal değerlendirildiği kullanılamıyor: anomali özeti okunamıyor \(rolünüz bunu okuyamıyor\)\./),
    ).toBeInTheDocument();
    view.unmount();
    setup({ [SOURCES]: ok([]) });
    render(<LogSourceHealth />);
    expect(screen.getByText(/İzlenen log source yok\. Hacim baseline'ını/)).toBeInTheDocument();
  });

  it("says loading and forbidden in Turkish", () => {
    setup({ [SOURCES]: loading });
    const view = render(<LogSourceHealth />);
    expect(screen.getByRole("status")).toHaveTextContent("Kaynak durumları yükleniyor…");
    view.unmount();
    setup({ [SOURCES]: forbidden });
    render(<LogSourceHealth />);
    expect(screen.getByRole("status")).toHaveTextContent("Rolünüz bunu okuyamıyor: kaynak durumları (izin: read:anomalies)");
  });
});

describe("RecentAnomalies in Turkish", () => {
  const setup = (list: unknown, sum: unknown = ok(summary({ insufficient_data_sources: 3 }))) => {
    res.byKey = { [ACTIVE]: list, [ANOM_SUM]: sum, [WAF]: list };
  };

  it("badges the total, words the headers and puts severity and state in Turkish capitals", () => {
    setup(ok(page([anomaly({ severity: "HIGH", state: "RECOVERING", log_source_name: "FW 7" })], 18)));
    render(<RecentAnomalies />);
    expect(screen.getByText("18 etkin")).toBeInTheDocument();
    expect(screen.getByText("şu anda etkin")).toBeInTheDocument();
    for (const header of ["Tespit zamanı", "Anomali", "Önem, durum", "beklenenden"]) {
      expect(screen.getByText(header)).toBeInTheDocument();
    }
    expect(screen.getAllByText("YÜKSEK").length).toBeGreaterThan(0);
    expect(screen.getAllByText("DÜZELİYOR").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "FW 7 anomalisini incele" })).toBeInTheDocument();
    expect(screen.getByText("Son 1 / 18 anomali gösteriliyor.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Tümünü görüntüle" })).toBeInTheDocument();
  });

  it("states that an empty list is not all clear, with the not-judged count", () => {
    setup(ok(page([])));
    render(<RecentAnomalies />);
    expect(
      screen.getByText("Etkin anomali yok. Baseline'ı olmayan kaynaklar değerlendirilmiyor: 3 kaynak."),
    ).toBeInTheDocument();
  });

  it("says when the not-judged count could not be read, and when it is still loading", () => {
    setup(ok(page([])), forbidden);
    const view = render(<RecentAnomalies />);
    expect(screen.getByText("Etkin anomali yok. Kaç kaynağın değerlendirilmediği okunamadı.")).toBeInTheDocument();
    view.unmount();
    setup(ok(page([])), loading);
    render(<RecentAnomalies />);
    expect(screen.getByRole("status")).toHaveTextContent("Değerlendirilmeyen kaynak sayısı yükleniyor…");
  });

  it("names the WAF list and explains its empty state in Turkish", () => {
    setup(ok(page([])));
    render(<RecentAnomalies subject="WAF_ENDPOINT" />);
    expect(screen.getByRole("heading", { name: /Son WAF anomalileri/ })).toBeInTheDocument();
    expect(screen.getByText(/Etkin WAF endpoint anomalisi yok\. Geçerli profili olmayan WAF kaynakları toplanmaz/)).toBeInTheDocument();
  });

  it("says forbidden and failed in Turkish", () => {
    setup(forbidden);
    const view = render(<RecentAnomalies />);
    expect(screen.getByRole("status")).toHaveTextContent("Rolünüz bunu okuyamıyor: etkin anomaliler (izin: read:anomalies)");
    view.unmount();
    setup(failed(503));
    render(<RecentAnomalies />);
    expect(screen.getByRole("alert")).toHaveTextContent("Okunamadı: etkin anomaliler.");
  });

  it("shows a value it has no label for as it was sent", () => {
    setup(ok(page([anomaly({ severity: "SEVERE" })])));
    render(<RecentAnomalies />);
    expect(screen.getAllByText("SEVERE").length).toBeGreaterThan(0);
  });
});

describe("RecentOffenses in Turkish", () => {
  const setup = (open: unknown, probe: unknown = ok(page([offenseRow()], 3))) => {
    res.byKey = { [OPEN_OFFENSES]: open, [PROBE]: probe };
  };

  it("words the headers and shows the status as a Turkish word, the number as a number", () => {
    setup(ok(page([offenseRow({ qradar_offense_id: 1204, status: "OPEN", magnitude: 7, description: null })], 12)));
    render(<RecentOffenses />);
    expect(screen.getByRole("heading", { name: /Son offense'lar/ })).toBeInTheDocument();
    expect(screen.getByText("açık, son güncellemeye göre")).toBeInTheDocument();
    for (const header of ["Son güncelleme", "Offense", "Büyüklük"]) {
      expect(screen.getByText(header)).toBeInTheDocument();
    }
    expect(screen.getByText("10 üzerinden")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1204 numaralı offense" })).toBeInTheDocument();
    expect(screen.getByText(/#1204 · Açık/)).toBeInTheDocument();
    expect(screen.getByText("Son 1 / 12 offense gösteriliyor.")).toBeInTheDocument();
  });

  it("writes a missing magnitude as a dash, not a zero", () => {
    setup(ok(page([offenseRow({ magnitude: null })])));
    render(<RecentOffenses />);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("Büyüklük kaydedilmedi")).toBeInTheDocument();
    expect(row.querySelector(".tw-measure")).toBeNull();
  });

  it("says NOT_COLLECTED, and the empty case, in Turkish", () => {
    setup(ok(page([])), ok(page([], 0)));
    const view = render(<RecentOffenses />);
    expect(screen.getByText("Toplanmadı")).toBeInTheDocument();
    expect(screen.getByText(/Henüz offense kesiti toplanmadı\./)).toBeInTheDocument();
    view.unmount();
    setup(ok(page([])), ok(page([offenseRow()], 3)));
    render(<RecentOffenses />);
    expect(screen.getByText("Son kesitte açık offense yok.")).toBeInTheDocument();
  });

  it("says a probe that could not confirm the snapshot, forbidden and loading", () => {
    setup(ok(page([])), failed(500));
    const view = render(<RecentOffenses />);
    expect(screen.getByText(/Bir offense kesitinin var olduğu doğrulanamadı\./)).toBeInTheDocument();
    view.unmount();
    setup(forbidden);
    const second = render(<RecentOffenses />);
    expect(screen.getByRole("status")).toHaveTextContent("Rolünüz bunu okuyamıyor: açık offense'lar (izin: read:offenses)");
    second.unmount();
    setup(ok(page([])), loading);
    render(<RecentOffenses />);
    expect(screen.getByRole("status")).toHaveTextContent("Offense kesiti durumu yükleniyor…");
  });

  it("shows a status it has no label for as it was sent", () => {
    setup(ok(page([offenseRow({ status: "ARCHIVED" })])));
    render(<RecentOffenses />);
    expect(screen.getByText(/· ARCHIVED/)).toBeInTheDocument();
  });
});
