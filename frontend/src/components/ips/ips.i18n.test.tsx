// The IPS area in Turkish (D-37): the label tables, the figures and units, the investigation
// with its honesty states, the chart's axis, tooltip and ticks, the Dashboard widget and the
// sources administration. English stays what it was: every helper keeps its English default.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { failed, forbidden, ok } from "@/components/home/testkit";
import { createTextTranslator } from "@/i18n/textTranslator";
import { ApiError, fleetApi, ipsApi, type FleetSource, type Page } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { page } from "@/lib/home/fixtures";

import tr from "../../../messages/tr";
import AdminLoading from "@/app/admin/ips-sources/loading";
import AdminError from "@/app/admin/ips-sources/error";
import IpsSourcesPage from "@/app/admin/ips-sources/page";
import { AddIpsSourcePicker } from "@/app/admin/ips-sources/AddIpsSourcePicker";
import { IpsProfileEditor } from "@/app/admin/ips-sources/IpsProfileEditor";
import { candidate, catalog, HISTORY, IPS_SCHEMA, profile, status } from "@/app/admin/ips-sources/fixtures";
import {
  genericNameNotes,
  ipsFieldStatusInfo,
  ipsHistoryActionLabel,
  ipsOutcomeNote,
  ipsReasonText,
} from "@/app/admin/ips-sources/ips";
import { readError } from "@/app/admin/ips-sources/readError";
import { IPS_ANOMALIES_LIMIT, mergeIpsAnomalies } from "@/components/catalog/ipsAnomalies";
import { RecentIpsAnomaliesView } from "@/components/catalog/RecentIpsAnomalies";

import {
  ipsCell,
  ipsDetail,
  ipsDetection,
  ipsEntityBucket,
  ipsListItem,
  ipsSourceDetail,
} from "./fixtures";
import { ipsFigures, ofText, quantityText } from "./ipsDetectors";
import { formatEvents, formatFigure, formatPointChange, unitText } from "./ipsFormat";
import {
  dimensionNoun,
  entityLabel,
  ipsStateMeaning,
  ipsStateWord,
  ipsSubjectText,
  ipsTypeLabel,
  IPS_TYPES,
  rowTypeLabel,
} from "./ipsLabels";
import { ipsChartSummary, IpsInvestigation } from "./IpsInvestigation";
import { IpsSeriesChart, measurementMeaning } from "./IpsSeriesChart";
import { buildIpsEntitySeries } from "./ipsSeries";
import { summarizeIpsAnomaly } from "./ipsSummary";

const NB = "\u00a0";
const turkish: BehaviorI18n = { t: createTextTranslator("tr", tr), locale: "tr" };

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return { setOption, init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn() })) };
});
vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use: vi.fn() };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

beforeEach(() => {
  vi.restoreAllMocks();
  setOption.mockClear();
  init.mockClear();
  setTestLocale("tr");
});

describe("IPS helpers in Turkish", () => {
  it("words every detector type, and shows an unknown type as sent", () => {
    for (const type of IPS_TYPES) {
      const label = ipsTypeLabel(type, turkish);
      expect(label).not.toBe(type);
      expect(label).not.toBe("");
    }
    expect(ipsTypeLabel("IPS_PORT_RATE_SPIKE", turkish)).toBe("Hedef port hızı artışı");
    expect(ipsTypeLabel("IPS_FUTURE", turkish)).toBe("IPS_FUTURE");
    // A Phase A type on an IPS row never borrows its own name.
    expect(ipsTypeLabel("VOLUME_SPIKE", turkish)).toBe("VOLUME_SPIKE");
    expect(rowTypeLabel({ subject_kind: "IPS_ENTITY", anomaly_type: "IPS_FUTURE" }, turkish)).toBe(
      "IPS_FUTURE",
    );
    // English default is untouched.
    expect(ipsTypeLabel("IPS_PORT_RATE_SPIKE")).toBe("Destination-port rate spike");
  });

  it("words dimensions and states, and never reads a prototype name as a message", () => {
    expect(dimensionNoun("DESTINATION_PORT", turkish)).toBe("Hedef port");
    expect(dimensionNoun("constructor", turkish)).toBe("constructor");
    expect(dimensionNoun("toString", turkish)).toBe("toString");
    expect(ipsStateWord("OPEN", turkish)).toBe("Açık");
    expect(ipsStateWord("constructor", turkish)).toBe("constructor");
    expect(ipsStateMeaning("constructor", "entity", turkish)).toBe("");
    expect(ipsStateMeaning("INSUFFICIENT_DATA", "entity", turkish)).toContain("bu varlık");
    expect(ipsStateMeaning("INSUFFICIENT_DATA", "source", turkish)).toContain("bu IPS kaynağı");
    expect(ipsStateMeaning("INSUFFICIENT_DATA", "source")).toContain("this IPS source");
    expect(ipsSubjectText({ subject_kind: "IPS_SOURCE", ips_entity: null }, turkish)).toBe(
      "Kaynak düzeyi",
    );
    expect(ipsSubjectText({ subject_kind: "IPS_ENTITY", ips_entity: null }, turkish)).toBe(
      "Varlık kaydedilmedi",
    );
    expect(
      entityLabel(
        { dimension: "SIGNATURE", value_key: "qid:5000123", display_name: null },
        turkish,
      ),
    ).toBe("İmza QID 5000123");
  });

  it("writes figures and units in the Turkish number form", () => {
    expect(formatEvents(1204, null, "tr")).toBe("1.204");
    expect(formatEvents(120.5, null, "tr")).toBe("120,5");
    expect(formatEvents(1204)).toBe("1,204");
    expect(formatFigure("share", 0.318, null, turkish)).toContain("31,8");
    expect(formatFigure("events", null, null, turkish)).toBe("—");
    expect(formatPointChange(44, turkish)).toBe(`+44,0${NB}puan`);
    expect(formatPointChange(44)).toBe(`+44.0${NB}pp`);
    expect(unitText("events", "5 min", turkish)).toBe(`olay / 5${NB}dk`);
    expect(unitText("events", "1 h", turkish)).toBe(`olay / 1${NB}sa`);
    expect(unitText("events", "30 s", turkish)).toBe(`olay / 30${NB}sn`);
    expect(unitText("events", null, turkish)).toBe("olay / zaman dilimi");
    expect(unitText("ports", null, turkish)).toBe("farklı port");
    expect(unitText("events", "5 min")).toBe("events / 5 min");
  });

  it("words the measured quantity, falling back to English for an unknown detector", () => {
    expect(quantityText("IPS_BLOCK_RATIO_SHIFT", turkish)).toBe("Eylem taşıyan olayların engellenen payı");
    expect(ofText("IPS_BLOCK_RATIO_SHIFT", turkish)).toBe("eylem taşıyan olaylar");
    expect(quantityText("IPS_FUTURE", turkish)).toBe("");
    expect(ofText("IPS_PORT_RATE_SPIKE", turkish)).toBeNull();
  });

  it("builds the summary from codes, never from English text", () => {
    const entity = summarizeIpsAnomaly(ipsDetail(), 300, turkish);
    expect(entity.text).toBe(
      `Ölçüm, Hedef port 445/tcp için 940 olay / 5${NB}dk değerine ulaştı, beklenen değer 120 olay / 5${NB}dk.`,
    );
    const share = summarizeIpsAnomaly(ipsSourceDetail(), 300, turkish);
    expect(share.text).toContain("%62,0");
    expect(share.text).toContain(`620${NB}/${NB}1.000 olay`);
    const lower = summarizeIpsAnomaly(
      ipsDetail({ detection: ipsDetection({ observed_measurement: "LOWER_BOUND" }) }),
      300,
      turkish,
    );
    expect(lower.caveat).toContain("alt sınır");
    expect(summarizeIpsAnomaly(ipsDetail({ anomaly_type: "IPS_FUTURE" }), 300, turkish).text).toContain(
      "IPS_FUTURE",
    );
  });

  it("words the chart's measurement meanings and keeps an unknown one as sent", () => {
    expect(measurementMeaning("EXACT", turkish)).toBe("kesin");
    expect(measurementMeaning(null, turkish)).toContain("eksik");
    expect(measurementMeaning("SOMETHING", turkish)).toBe("SOMETHING");
    expect(measurementMeaning("EXACT")).toBe("exact");
  });

  it("writes the chart summary in Turkish with grouped counts", () => {
    const points = buildIpsEntitySeries(
      Array.from({ length: 1204 }, (_, i) =>
        ipsEntityBucket({ bucket_start: new Date(Date.UTC(2026, 9, 12, 0, i * 5)).toISOString() }),
      ),
    );
    const text = ipsChartSummary(ipsDetail(), points, "Olay", turkish);
    expect(text).toContain("1.204 saklanmış zaman diliminde");
    expect(text).toContain("zaman dilimi başına 70,0 ile 210,0 olay");
  });

  it("figures stay numbers the helper reads (units of a new port)", () => {
    expect(ipsFigures(ipsDetail({ anomaly_type: "IPS_NEW_DESTINATION_PORT" }), null).unit).toBe("since");
  });
});

describe("IPS investigation in Turkish", () => {
  const series = (n = 3) => ({
    kind: "entity" as const,
    buckets: Array.from({ length: n }, (_, i) =>
      ipsEntityBucket({ bucket_start: new Date(Date.UTC(2026, 9, 12, 9, i * 5)).toISOString() }),
    ),
  });

  it("renders the header, the measures and the section names in Turkish", () => {
    render(<IpsInvestigation anomaly={ipsDetail()} series={series()} baselines={null} canOpenSource />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Hedef port 445/tcp");
    expect(screen.getByText("Hedef port hızı artışı")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "lab-ips-01" })).toBeInTheDocument();
    const measures = within(screen.getByLabelText("Vaka ölçümleri"));
    expect(measures.getByText("Gözlenen")).toBeInTheDocument();
    expect(measures.getByText("7,83x")).toBeInTheDocument();
    expect(measures.getByText("7,83x").closest("div")).toHaveTextContent("beklenene göre +820 olay");
    for (const name of ["Zaman çizelgesi", "Değerlendirilenler", "Kanıt", "Yaşam döngüsü"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("Tüm anomaliler", { exact: false })).toBeInTheDocument();
    // The state pill is the state code in Turkish capitals; the API's own sentence stays English.
    expect(screen.getAllByText("AÇIK").length).toBeGreaterThan(0);
    expect(screen.getByText(/940 events in the 09:00 bucket/)).toBeInTheDocument();
  });

  it("says PARTIAL, TRUNCATED, maintenance and INSUFFICIENT_DATA in Turkish", () => {
    const buckets = [
      ipsEntityBucket({ bucket_start: "2026-10-12T09:00:00Z", measurement: "LOWER_BOUND" }),
      ipsEntityBucket({ bucket_start: "2026-10-12T09:05:00Z", measurement: "NOT_COLLECTED", events: 0 }),
      ipsEntityBucket({ bucket_start: "2026-10-12T09:10:00Z", admission_truncated: true }),
      ipsEntityBucket({ bucket_start: "2026-10-12T09:15:00Z", in_maintenance: true }),
    ];
    render(
      <IpsInvestigation
        anomaly={ipsDetail()}
        series={{ kind: "entity", buckets }}
        baselines={[ipsCell({ is_reliable: false })]}
      />,
    );
    expect(screen.getByText(/KISMİ/)).toBeInTheDocument();
    expect(screen.getByText(/2 \/ 4 saklanmış zaman dilimi tam olarak ölçülmedi/)).toBeInTheDocument();
    expect(screen.getByText(/KIRPILMIŞ/)).toBeInTheDocument();
    expect(screen.getAllByText(/1 zaman dilimi bakımdaydı/)[0]).toBeInTheDocument();
    expect(screen.getByText(/YETERSİZ VERİ/)).toBeInTheDocument();
    expect(screen.getByText(/henüz güvenilir bir sezonluk hücresi yok/)).toBeInTheDocument();
  });

  it("says BASELINE UNAVAILABLE, a failed series and an empty window in Turkish", () => {
    const { unmount } = render(
      <IpsInvestigation anomaly={ipsDetail()} series={series()} baselines={null} />,
    );
    expect(screen.getByText(/BASELINE KULLANILAMIYOR/)).toBeInTheDocument();
    unmount();
    const failedView = render(<IpsInvestigation anomaly={ipsDetail()} series={null} baselines={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Bu varlık için ölçüm geçmişi yüklenemedi");
    failedView.unmount();
    render(<IpsInvestigation anomaly={ipsDetail()} series={series(0)} baselines={null} />);
    expect(screen.getByText(/hiçbir zaman dilimi saklanmadı/)).toBeInTheDocument();
    expect(screen.getByText(/sıfır olay gözlemi değildir/)).toBeInTheDocument();
  });

  it("words the source-level detection block, with a missing value as a reason", () => {
    render(
      <IpsInvestigation
        anomaly={ipsSourceDetail({ detection: ipsDetection({ robust_score_status: "DEGENERATE", robust_z: null, threshold: null }) })}
        series={{ kind: "source", buckets: [] }}
        baselines={null}
      />,
    );
    expect(screen.getByText("Robust puanı DEJENERE.")).toBeInTheDocument();
    expect(screen.getByText(/kullanılmadı: MAD sıfırdı/)).toBeInTheDocument();
    expect(screen.getByText(/eşik kaydedilmedi/)).toBeInTheDocument();
    expect(screen.getByText(/IPS kaynağı için hiçbir zaman dilimi saklanmadı/)).toBeInTheDocument();
    expect(screen.getByText(/sıfır pay gözlemi değildir/)).toBeInTheDocument();
  });

  it("a detection block that was never recorded says it is a missing record", () => {
    render(<IpsInvestigation anomaly={ipsDetail({ detection: null })} series={series()} baselines={null} />);
    expect(screen.getByText(/algılayıcı hesabı kaydedilmedi/)).toBeInTheDocument();
  });
});

describe("IPS chart in Turkish", () => {
  const points = buildIpsEntitySeries([
    ipsEntityBucket({ bucket_start: "2026-10-12T09:00:00Z", events: 1204 }),
    ipsEntityBucket({ bucket_start: "2026-10-12T09:05:00Z", events: 1500.5 }),
  ]);
  const props = {
    points,
    rows: [],
    unit: "events" as const,
    quantity: "Olay",
    bucketSeconds: 300,
    expected: 120,
    expectedLow: 70,
    expectedHigh: 210,
    baseline: null,
    ariaLabel: "x",
  };

  it("names the axes with their unit, groups the ticks and words the tooltip", () => {
    render(<IpsSeriesChart {...props} />);
    const option = setOption.mock.calls[0][0] as {
      xAxis: { name: string };
      yAxis: { name: string; axisLabel: { formatter: (v: number) => string } };
      tooltip: { formatter: (p: unknown) => string };
    };
    expect(option.xAxis.name).toBe("Zaman (UTC)");
    expect(option.yAxis.name).toBe(`Olay (olay / 5${NB}dk)`);
    expect(option.yAxis.axisLabel.formatter(1204)).toBe("1.204");
    const tip = option.tooltip.formatter({ dataIndex: 1 });
    expect(tip).toContain("1.500,5");
    expect(tip).toContain("Ölçüm: kesin");
    expect((init.mock.calls[0] as unknown[])[2]).toMatchObject({ locale: expect.anything() });
    expect(screen.getByRole("list", { name: "Grafik anahtarı" })).toBeInTheDocument();
    expect(screen.getByText("Tespit anındaki beklenen aralık", { exact: false })).toBeInTheDocument();
  });

  it("stays English with the English interface", () => {
    setTestLocale("en");
    render(<IpsSeriesChart {...props} quantity="Events" />);
    const option = setOption.mock.calls[0][0] as { xAxis: { name: string }; yAxis: { name: string } };
    expect(option.xAxis.name).toBe("Time (UTC)");
    expect(option.yAxis.name).toBe("Events (events / 5 min)");
  });
});

describe("Recent IPS anomalies widget in Turkish", () => {
  const entity = ipsListItem({ id: "e1" });
  const merged = (n = 1, total = 1) =>
    [page([entity], total), page(Array.from({ length: n - 1 }, () => ipsListItem({ id: "s" })), 0)] as never;

  it("words the populated table with the Turkish count form", () => {
    render(<RecentIpsAnomaliesView state={ok(merged(1, 1204))} />);
    expect(screen.getByRole("table", { name: "Son IPS anomalileri" })).toBeInTheDocument();
    expect(screen.getByText("Hedef port 445/tcp")).toBeInTheDocument();
    expect(screen.getByText(/Hedef port hızı artışı · lab-ips-01/)).toBeInTheDocument();
    expect(screen.getByText("Gözlenen → beklenen")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Hedef port 445/tcp incelemesi" })).toBeInTheDocument();
    expect(screen.getByText(/En yeni 1 \/ 1\.204 gösteriliyor/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Etkin tüm IPS varlık ve kaynak anomalilerini gör" })).toBeInTheDocument();
  });

  it("merges with the badge and the unit words in Turkish", () => {
    const view = mergeIpsAnomalies(page([entity], 1204), page([], 0), IPS_ANOMALIES_LIMIT, turkish);
    expect(view.badge).toBe("1.204 etkin");
    expect(view.rows[0].unitWords).toBe("olay / zaman dilimi");
    expect(view.shown).toBe(`En yeni 1${NB}/${NB}1.204 gösteriliyor`);
    const newPort = mergeIpsAnomalies(
      page([ipsListItem({ anomaly_type: "IPS_NEW_DESTINATION_PORT" })], 1),
      page([], 0),
      IPS_ANOMALIES_LIMIT,
      turkish,
    );
    expect(newPort.rows[0].expected).toBe("yok");
    expect(newPort.rows[0].unitWords).toBe("ilk görünmesinden beri olay");
    // The widget keeps its own English wording; the detail page's differs ("since it first appeared").
    const english = mergeIpsAnomalies(
      page([ipsListItem({ anomaly_type: "IPS_NEW_DESTINATION_PORT" })], 1),
      page([], 0),
    );
    expect(english.rows[0].unitWords).toBe("events since first appeared");
    expect(unitText("since", null)).toBe("events since it first appeared");
  });

  it("words empty, loading-failed and forbidden states, each linking to the owning route", () => {
    const empty = render(<RecentIpsAnomaliesView state={ok([page([]), page([])] as never)} />);
    expect(screen.getByText(/Etkin IPS anomalisi yok/)).toBeInTheDocument();
    expect(screen.getByText(/bu yüzden bu temiz bir sonuç değildir/)).toBeInTheDocument();
    empty.unmount();
    const bad = render(<RecentIpsAnomaliesView state={failed(500)} />);
    expect(screen.getByRole("link", { name: /Etkin tüm IPS/ })).toHaveAttribute(
      "href",
      "/anomalies?subject_kind=IPS&active_only=true",
    );
    expect(screen.getByText("Son IPS anomalileri")).toBeInTheDocument();
    bad.unmount();
    render(<RecentIpsAnomaliesView state={forbidden} />);
    expect(screen.getByText(/read:anomalies/)).toBeInTheDocument();
  });
});

describe("IPS sources administration in Turkish", () => {
  beforeEach(() => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue([candidate()]);
    vi.spyOn(fleetApi, "sources").mockResolvedValue({
      items: [{ instance_id: "inst-1" } as FleetSource],
      total: 1,
      limit: 1,
      offset: 0,
    } as Page<FleetSource>);
  });

  it("words the populated list, its columns and its notes", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([status(), status({ profile_id: "p-2", log_source_id: "ls-2", log_source_name: "pa", validated_at: null })]);
    render(await IpsSourcesPage());
    expect(screen.getByRole("heading", { level: 1, name: "IPS kaynakları" })).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Kaynak",
      "Profilsürüm",
      "Doğrulama",
      "Etkin",
      "Toplama",
      "Son doğrulama (UTC)",
    ]);
    expect(screen.getAllByText("Etkin").length).toBeGreaterThan(0);
    expect(screen.getAllByText("QRadar id 413 · Cisco Firepower Threat Defense").length).toBe(2);
    expect(screen.getByText("hiç doğrulanmadı")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "IPS kaynağı ekle" })).toBeInTheDocument();
  });

  it("words the empty list", async () => {
    vi.spyOn(ipsApi, "sources").mockResolvedValue([]);
    render(await IpsSourcesPage());
    expect(screen.getByText("Henüz hiçbir IPS kaynağının profili yok.")).toBeInTheDocument();
  });

  it("words a forbidden read with the permission, an expired session and a failure", async () => {
    vi.spyOn(ipsApi, "sources").mockRejectedValue(new ApiError(403, "no"));
    const forbiddenView = render(await IpsSourcesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("izin: admin:ips");
    expect(screen.getByRole("alert")).toHaveTextContent("IPS kaynakları gösterilemedi.");
    forbiddenView.unmount();
    vi.spyOn(ipsApi, "sources").mockRejectedValue(new ApiError(500, "boom"));
    render(await IpsSourcesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Okunamadı: IPS kaynakları.");
    expect(readError(new ApiError(401, "x"), "x", turkish)).toContain("Oturumunuzun süresi doldu");
    expect(readError(new ApiError(500, "x"), "sources")).toContain("Sources could not be loaded");
  });

  it("words the loading and route error states", () => {
    const loading = render(<AdminLoading />);
    expect(loading.container.textContent).toContain("IPS kaynakları");
    loading.unmount();
    render(<AdminError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByText(/IPS kaynakları gösterilemedi/)).toBeInTheDocument();
  });

  it("words the picker: candidates, empty and forbidden", async () => {
    const user = userEvent.setup();
    vi.spyOn(ipsApi, "candidates").mockResolvedValueOnce([candidate()]);
    const view = render(<AddIpsSourcePicker instanceId="inst-1" />);
    await user.click(screen.getByRole("button", { name: "IPS kaynağı ekle" }));
    expect(await screen.findByText("IPS olarak sınıflandırıldı")).toBeInTheDocument();
    expect(screen.getByLabelText("Log source'ları ada göre ara")).toBeInTheDocument();
    view.unmount();
    vi.spyOn(ipsApi, "candidates").mockRejectedValueOnce(new ApiError(403, "no"));
    render(<AddIpsSourcePicker instanceId="inst-1" />);
    await user.click(screen.getByRole("button", { name: "IPS kaynağı ekle" }));
    expect(await screen.findByText(/izin: admin:ips/)).toBeInTheDocument();
  });

  it("words the validation outcomes and history actions built in the frontend", () => {
    expect(ipsHistoryActionLabel("ips.profile.validated", turkish)).toBe("Doğrulandı");
    expect(ipsHistoryActionLabel("ips.profile.unknown", turkish)).toBe("ips.profile.unknown");
    expect(ipsHistoryActionLabel("constructor", turkish)).toBe("constructor");
    expect(ipsHistoryActionLabel("ips.profile.validated")).toBe("Validated");
    const entry = {
      action: "ips.profile.validated",
      outcome: "SOURCE_BUSY",
    } as Parameters<typeof ipsOutcomeNote>[0];
    expect(ipsOutcomeNote(entry, turkish)).toContain("kaynak kilidini alamadı");
    expect(ipsOutcomeNote({ ...entry, outcome: "FAILURE" }, turkish)).toContain("tamamlanamadı");
    expect(ipsOutcomeNote({ ...entry, outcome: "SUCCESS" }, turkish)).toBeNull();
    const notes = genericNameNotes(
      { warnings: [], fields: [{ field: "SIGNATURE", generic_name_ratio: 0.12 }] } as unknown as Parameters<typeof genericNameNotes>[0],
      turkish,
    );
    expect(notes[0].text).toContain("%12,0");
    expect(
      ipsReasonText("the bounded validation probe was truncated", IPS_SCHEMA, () => null, turkish),
    ).toContain("sonuçsuz");
  });
});

describe("IPS profile editor in Turkish", () => {
  const SOURCE = {
    id: "ls-413",
    name: "CiscoFirepowerThreatDefense @ ftd-01",
    qradarId: 413,
    typeName: "Cisco Firepower Threat Defense",
  };
  const renderEditor = () =>
    render(
      <IpsProfileEditor
        schema={IPS_SCHEMA}
        source={SOURCE}
        profile={profile()}
        catalog={catalog()}
        history={HISTORY}
      />,
    );

  it("words the editor header states through the I18N-8 helpers", () => {
    renderEditor();
    expect(screen.getByText("Geçerli (v2)")).toBeInTheDocument();
    expect(screen.getAllByText("Toplanıyor").length).toBeGreaterThan(0);
    expect(screen.queryByText("Collecting")).not.toBeInTheDocument();
  });

  it("builds the values sentence without lowercasing a Turkish outcome label", () => {
    const view = renderEditor();
    const subtitle = view.container.querySelector("#fm-values-h + .subtitle");
    expect(subtitle?.textContent).toMatch(/^Ham /);
    expect(subtitle?.textContent).toContain("hangi sonuca eşleneceği: Engellendi ve İzin verildi.");
    expect(subtitle?.textContent).not.toContain("izin verildi ve");
  });

  it("keeps the English values sentence lower case", () => {
    setTestLocale("en");
    const view = renderEditor();
    expect(view.container.querySelector("#fm-values-h + .subtitle")?.textContent).toContain(
      "count as blocked and allowed.",
    );
  });

  it("words the field statuses of the shared table in Turkish, not only the IPS-specific one", () => {
    const ctx = { populatedRatio: 0.9, plausibleRatio: null, requiredCoverage: 0.95, usedByCollection: true };
    expect(ipsFieldStatusInfo("AVAILABLE", ctx, turkish).label).toBe("Kullanılabilir");
    expect(ipsFieldStatusInfo("NOT_MAPPED", ctx, turkish).label).toBe("Eşlenmedi");
    expect(ipsFieldStatusInfo("UNPOPULATED", ctx, turkish).label).not.toBe("Unpopulated");
    expect(ipsFieldStatusInfo("AVAILABLE", ctx).label).toBe("Usable");
    expect(
      ipsReasonText("SOURCE_IP: NOT_MAPPED (no field)", IPS_SCHEMA, () => null, turkish),
    ).not.toMatch(/mapped/i);
  });
});
