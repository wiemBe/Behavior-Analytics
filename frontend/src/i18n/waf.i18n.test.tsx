// I18N-9: the WAF behavior, investigation, administration and dashboard surfaces in Turkish.
// Existing colocated tests pin the unchanged English contract; these tests pin translated states,
// backend values that deliberately remain verbatim, and Turkish number punctuation.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import WafSourcesPage from "@/app/admin/waf-sources/page";
import AdminLoading from "@/app/admin/waf-sources/loading";
import WafBehaviorError from "@/app/behavior/waf/error";
import WafBehaviorPage from "@/app/behavior/waf/page";
import WafSourceEndpointsPage from "@/app/behavior/waf/sources/[id]/page";
import { RecentWafAnomalies } from "@/components/catalog/RecentWafAnomalies";
import { WafEndpointTraffic } from "@/components/catalog/WafEndpointTraffic";
import { WafSources } from "@/components/catalog/WafSources";
import { ok, failed, forbidden, loading } from "@/components/home/testkit";
import {
  WAF_ENDPOINT_ID,
  WAF_SOURCE_ID,
  wafBaselineCell,
  wafBucket,
  wafDetail,
  wafEndpoint,
  wafListItem,
  wafRatioDetection,
  wafSource,
} from "@/components/waf/fixtures";
import { WafInvestigation } from "@/components/waf/WafInvestigation";
import { bucketWidth, formatPoints, formatRequests, requestsUnit } from "@/components/waf/wafFormat";
import { anomalyTypeLabel } from "@/components/waf/wafLabels";
import { parseWafRange, wafRangeLabel } from "@/components/waf/wafRange";
import { validationView } from "@/components/waf/wafSourceHealth";
import { summarizeWafAnomaly } from "@/components/waf/wafSummary";
import { createTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, homeReads } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { SCHEMA, status } from "@/components/fieldmapping/fixtures";
import en from "../../messages/en";
import tr from "../../messages/tr";

const TR: BehaviorI18n = { t: createTextTranslator("tr", tr as never), locale: "tr" };

const chartProps = vi.hoisted(() => ({ last: null as null | Record<string, unknown> }));
vi.mock("@/components/waf/WafRequestChart", () => ({
  WafRequestChart: (props: Record<string, unknown>) => {
    chartProps.last = props;
    return <div role="img" aria-label={String(props.ariaLabel ?? "chart")}>{String(props.textSummary ?? "")}</div>;
  },
}));

const resources = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => key === null ? { status: "idle" } : (resources.byKey[key] ?? loading),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
  resources.byKey = {};
  chartProps.last = null;
});

describe("WAF helpers in Turkish", () => {
  it("localizes every detector label and preserves an unknown code", () => {
    expect(anomalyTypeLabel("WAF_REQUEST_RATE_SPIKE", TR)).toBe("İstek hızı artışı");
    expect(anomalyTypeLabel("WAF_BLOCK_RATIO_SPIKE", TR)).toBe("Engelleme oranı artışı");
    expect(anomalyTypeLabel("WAF_NEW_ENDPOINT_ACCESS", TR)).toBe("Yeni endpoint erişimi");
    expect(anomalyTypeLabel("WAF_FUTURE_KIND", TR)).toBe("WAF_FUTURE_KIND");
    expect(anomalyTypeLabel("WAF_REQUEST_RATE_SPIKE")).toBe(en.enums.anomalyType.WAF_REQUEST_RATE_SPIKE);
  });

  it("formats counts, points, units and ranges with Turkish punctuation", () => {
    expect(formatRequests(1204.5, "EXACT", "tr")).toBe("1.204,5");
    expect(formatRequests(1204, "LOWER_BOUND", "tr")).toBe("≥ 1.204");
    expect(formatPoints(29.7, TR)).toBe("+29,7\u00a0puan");
    expect(bucketWidth(300, TR)).toBe("5\u00a0dk");
    expect(requestsUnit(300, TR)).toBe("istek\u00a0/\u00a05\u00a0dk");
    expect(wafRangeLabel(parseWafRange("7d"), TR)).toBe("7\u00a0gün");
  });

  it("localizes validation honesty and a ratio summary while leaving backend data intact", () => {
    expect(validationView(wafSource(), TR)).toEqual({
      label: "GEÇERLİ",
      tone: "ok",
      detail: "profil v3 için",
    });
    expect(validationView(wafSource({ validation_status: "VALID", validation_current: false }), TR).label)
      .toBe("ESKİ");
    expect(validationView(wafSource({ validation_status: "INCONCLUSIVE" }), TR).label)
      .toBe("SONUÇSUZ");
    const summary = summarizeWafAnomaly(wafDetail({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
      observed_value: 0.318,
      expected_value: 0.021,
      detection: wafRatioDetection(),
    }), null, TR);
    expect(summary.text).toContain("%31,8");
    expect(summary.text).toContain("318\u00a0/\u00a01.000 istek");
  });
});

describe("WAF behavior routes in Turkish", () => {
  const page = (items: ReturnType<typeof wafSource>[]) => ({
    items, total: items.length, limit: 50, offset: 0,
    range_start: "2026-10-12T09:00:00Z", range_end: "2026-10-13T09:00:00Z",
  });

  it("renders the populated source table, INSUFFICIENT_DATA equivalent and Turkish totals", async () => {
    vi.spyOn(api, "wafBehaviorSources").mockResolvedValue(page([
      wafSource({ active_endpoints: 1204 }),
      wafSource({
        log_source_id: "s-2", log_source_name: "öğrenen",
        validation_status: "INCONCLUSIVE", validation_current: false, latest_bucket: null,
      }),
    ]));
    render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF davranışı");
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("1.204 / 500")).toBeInTheDocument();
    expect(screen.getByText("SONUÇSUZ")).toBeInTheDocument();
    expect(screen.getByText("Henüz zaman dilimi toplanmadı")).toBeInTheDocument();
  });

  it("renders empty, forbidden and boundary errors in Turkish", async () => {
    vi.spyOn(api, "wafBehaviorSources").mockResolvedValue(page([]));
    const empty = render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("status")).toHaveTextContent("Hiçbir WAF kaynağının davranış profili yok");
    empty.unmount();
    vi.spyOn(api, "wafBehaviorSources").mockRejectedValue(new ApiError(403, "no"));
    const denied = render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("WAF davranışını görüntüleme izniniz yok");
    denied.unmount();
    render(<WafBehaviorError error={new Error("secret")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("WAF davranışı gösterilemedi");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
  });

  it("renders the endpoint inventory and NOT_COLLECTED / PARTIAL alternatives", async () => {
    vi.spyOn(api, "wafBehaviorSource").mockResolvedValue(wafSource({ active_endpoints: 1204 }));
    vi.spyOn(api, "wafBehaviorEndpoints").mockResolvedValue({
      items: [
        wafEndpoint({ id: "retired", normalized_endpoint: "/old", is_active: false, retired_at: "2026-10-01T00:00:00Z" }),
        wafEndpoint({ id: "new", normalized_endpoint: "/new", state: null }),
      ], total: 1204, limit: 50, offset: 0,
    });
    const view = render(await WafSourceEndpointsPage({
      params: Promise.resolve({ id: WAF_SOURCE_ID }), searchParams: Promise.resolve({}),
    }));
    expect(screen.getByText("1.204 / 500 endpoint etkin")).toBeInTheDocument();
    expect(screen.getByText("Kullanım dışı")).toBeInTheDocument();
    expect(screen.getByText("Değerlendirilmedi")).toBeInTheDocument();
    // Pagination belongs to the finished shared-component wave; this route passes the real total.
    expect(screen.getByText("1–50 / 1204")).toBeInTheDocument();
    view.unmount();

    vi.spyOn(api, "wafBehaviorSource").mockResolvedValue(wafSource({ validation_current: false, enabled: false }));
    vi.spyOn(api, "wafBehaviorEndpoints").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    render(await WafSourceEndpointsPage({
      params: Promise.resolve({ id: WAF_SOURCE_ID }), searchParams: Promise.resolve({}),
    }));
    expect(screen.getByRole("status")).toHaveTextContent("Bu kaynak toplanmıyor");
    expect(screen.getByRole("status")).not.toHaveTextContent("normal");
  });
});

describe("WAF investigation in Turkish", () => {
  it("localizes a ratio anomaly, its numbers, lifecycle and partial history", () => {
    render(<WafInvestigation i18n={TR} anomaly={wafDetail({
      anomaly_type: "WAF_BLOCK_RATIO_SPIKE", observed_value: 0.318, expected_value: 0.021,
      detection: wafRatioDetection(), state: "INSUFFICIENT_DATA",
    })} buckets={[
      wafBucket({ request_count: 1204 }),
      wafBucket({ bucket_start: "2026-10-12T09:05:00Z", completeness: "PARTIAL", partial: true }),
    ]} baselines={[wafBaselineCell()]} />);
    expect(screen.getByText("Engelleme oranı artışı")).toBeInTheDocument();
    expect(screen.getByText("YETERSİZ VERİ")).toBeInTheDocument();
    expect(screen.getByLabelText("Vaka ölçümleri")).toHaveTextContent("%31,8");
    expect(screen.getByLabelText("Vaka ölçümleri")).toHaveTextContent("318 / 1.000");
    expect(chartProps.last?.textSummary).toContain("1 aralığın ölçülmüş oranı yoktu");
    expect(screen.getByText(/318 of 1000 requests carrying a WAF action/)).toBeInTheDocument();
  });
});

describe("WAF administration in Turkish", () => {
  beforeEach(() => {
    vi.spyOn(api, "wafMappingSchema").mockResolvedValue(SCHEMA);
    vi.spyOn(api, "wafCandidates").mockResolvedValue([]);
  });

  it("renders populated, empty, loading and failed list states", async () => {
    vi.spyOn(api, "wafSources").mockResolvedValue([status()]);
    const full = render(await WafSourcesPage());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF kaynakları");
    expect(screen.getByRole("columnheader", { name: "Doğrulama" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "WAF kaynağı ekle" })).toBeInTheDocument();
    full.unmount();

    vi.spyOn(api, "wafSources").mockResolvedValue([]);
    const empty = render(await WafSourcesPage());
    expect(screen.getByText("Hiçbir log source WAF kaynağı olarak yapılandırılmadı.")).toBeInTheDocument();
    empty.unmount();

    const wait = render(<AdminLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("WAF kaynakları yükleniyor");
    wait.unmount();

    vi.spyOn(api, "wafSources").mockRejectedValue(new ApiError(403, "secret"));
    render(await WafSourcesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("admin:waf");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
  });
});

describe("WAF dashboard widgets in Turkish", () => {
  it("renders WAF sources populated and empty, and the common stale and forbidden states", () => {
    const key = homeReads.wafSources().key;
    resources.byKey[key] = ok({ items: [wafSource({ active_endpoints: 1204 })], total: 1, limit: 200, offset: 0 });
    const full = render(<WafSources />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("WAF kaynakları");
    expect(screen.getByText("%97,2")).toBeInTheDocument();
    expect(screen.getByText("1.204")).toBeInTheDocument();
    full.unmount();

    resources.byKey[key] = ok({ items: [], total: 0, limit: 200, offset: 0 });
    const empty = render(<WafSources />);
    expect(screen.getByText(/Kayıtlı WAF kaynağı yok/)).toBeInTheDocument();
    empty.unmount();

    resources.byKey[key] = ok({ items: [wafSource()], total: 1, limit: 200, offset: 0 }, {
      receivedAt: "2020-01-01T00:00:00Z", refreshFailed: { httpStatus: 500 },
    });
    const stale = render(<WafSources />);
    expect(screen.getByText(/Son yenileme başarısız oldu/)).toBeInTheDocument();
    stale.unmount();

    resources.byKey[key] = forbidden;
    render(<WafSources />);
    expect(screen.getByRole("status")).toHaveTextContent("read:anomalies");
  });

  it("renders endpoint traffic unconfigured, empty, partial and failed states", () => {
    const cfg = { waf_source: WAF_SOURCE_ID, endpoint: WAF_ENDPOINT_ID };
    const endpointKey = homeReads.wafEndpointsOf(WAF_SOURCE_ID).key;
    const seriesKey = homeReads.wafEndpointSeries(WAF_ENDPOINT_ID, "24h").key;
    const cellsKey = homeReads.wafEndpointCells(WAF_ENDPOINT_ID).key;
    const unconfigured = render(<WafEndpointTraffic config={{ waf_source: null, endpoint: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Bir endpoint seçin");
    unconfigured.unmount();

    resources.byKey = {
      [endpointKey]: ok({ items: [wafEndpoint()], total: 1, limit: 200, offset: 0 }),
      [seriesKey]: ok({ buckets: [], total: 0, limit: 4000, truncated: false }),
      [cellsKey]: ok([wafBaselineCell()]),
    };
    const empty = render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText(/sıfır istek değildir/)).toBeInTheDocument();
    empty.unmount();

    resources.byKey[seriesKey] = ok({
      buckets: [wafBucket({ completeness: "PARTIAL", partial: true })], total: 1, limit: 4000, truncated: false,
    });
    const partial = render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByText(/Hiçbir zaman dilimi tam toplanmadı/)).toBeInTheDocument();
    partial.unmount();

    resources.byKey[seriesKey] = failed(500);
    render(<WafEndpointTraffic config={cfg} />);
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 500");
  });

  it("renders recent WAF anomalies with Turkish labels and its honest empty state", () => {
    const key = homeReads.activeWafAnomalies(5).key;
    resources.byKey[key] = ok({ items: [wafListItem({ deviation_ratio: 2.36 })], total: 1, limit: 5, offset: 0 });
    const full = render(<RecentWafAnomalies />);
    expect(screen.getByText(/İstek hızı artışı/)).toBeInTheDocument();
    expect(screen.getByText("2,36x")).toBeInTheDocument();
    full.unmount();
    resources.byKey[key] = ok({ items: [], total: 0, limit: 5, offset: 0 });
    render(<RecentWafAnomalies />);
    expect(screen.getByText(/Etkin WAF endpoint anomalisi yok/)).toBeInTheDocument();
  });
});
