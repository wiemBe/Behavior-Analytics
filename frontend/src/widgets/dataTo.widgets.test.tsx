// The widgets that can say how far their data reaches (UI-7.2): one per as-of shape. Each is
// fresh before twice its family's cadence and stale after it; a widget whose response has no
// data time is never stale, however old its answer.

import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EpsByClass } from "@/components/home/EpsByClass";
import { EpsChart } from "@/components/home/EpsChart";
import { KpiLogSources } from "@/components/home/KpiLogSources";
import { LogSourceHealth } from "@/components/home/LogSourceHealth";
import { ok } from "@/components/home/testkit";
import { cadence, wafSource } from "@/components/catalog/fixtures";
import { SOURCE, SRC, EP, epsCell, series } from "@/components/catalog/subjectFixtures";
import { SourceEps } from "@/components/catalog/SourceEps";
import { WafEndpointTraffic } from "@/components/catalog/WafEndpointTraffic";
import { WafSources } from "@/components/catalog/WafSources";
import { wafBaselineCell, wafEndpoint } from "@/components/waf/fixtures";
import { homeReads, type SearchCadenceItem, type SocOverview } from "@/lib/api";
import { behavior, epsPoint, epsSeries, fleet, fleetClass, summary } from "@/lib/home/fixtures";

import { CadenceProvider } from "./cadence";

const { setOption, init } = vi.hoisted(() => {
  const setOption = vi.fn();
  return {
    setOption,
    init: vi.fn(() => ({ setOption, resize: vi.fn(), dispose: vi.fn(), on: vi.fn() })),
  };
});
vi.mock("echarts/core", () => ({ init, use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {}, PieChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) =>
    key === null ? { status: "idle" } : (res.byKey[key] ?? { status: "loading" }),
}));

const NOW = Date.parse("2026-10-22T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();
const CADENCE = homeReads.searchCadence().key;
const phaseA = (over: Partial<SearchCadenceItem> = {}) =>
  cadence({ family: "PHASE_A_VOLUME", cadence_seconds: 300, bucket_seconds: 300, ...over });
const waf = (over: Partial<SearchCadenceItem> = {}) =>
  cadence({ family: "WAF_COLLECTION", cadence_seconds: 900, bucket_seconds: 300, ...over });

// A note's text is split by its <time> element: match the paragraph's whole text.
const note = (re: RegExp) =>
  screen.queryAllByText((_c, el) => el?.tagName === "P" && re.test(el.textContent ?? ""));
const stale = () => note(/^Stale:/);
const dataTo = () => note(/^Data to/);

const inFrame = (ui: React.ReactElement) => render(<CadenceProvider>{ui}</CadenceProvider>);

beforeEach(() => {
  res.byKey = { [CADENCE]: ok([phaseA(), waf()]) };
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  setOption.mockClear();
});
afterEach(() => vi.useRealTimers());

describe("window shape: the last measured step of a series (Fleet EPS)", () => {
  const key = homeReads.epsSeries("24h", "none").key;
  // Steps are 5 min wide; the series ends at the last measured step, not at the window's end.
  const points = (lastMeasuredStartAgo: number) => [
    { ...epsPoint(0, 5), step_start: ago(lastMeasuredStartAgo + 600) },
    { ...epsPoint(1, 6), step_start: ago(lastMeasuredStartAgo) },
    { ...epsPoint(2, null), step_start: ago(lastMeasuredStartAgo - 300) },
  ];

  it("is fresh while the last measured step ends inside twice the cadence", () => {
    // The step started 9 min ago and is 5 min wide: it ended 4 min ago.
    res.byKey[key] = ok(epsSeries(points(540)));
    inFrame(<EpsChart />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()[0]).toHaveTextContent("Data to 11:56:00 UTC, 4 min ago.");
  });

  it("is stale once it ended more than twice the cadence ago, and still draws", () => {
    // Started 20 min ago: ended 15 min ago, past 2 x 5 min.
    res.byKey[key] = ok(epsSeries(points(1200)));
    inFrame(<EpsChart />);
    expect(stale()[0]).toHaveTextContent(/Stale: the latest data is from 11:45:00 UTC, 15 min old\./);
    expect(setOption).toHaveBeenCalled();
  });

  it("ignores the request's own window_end, which is not a data time", () => {
    res.byKey[key] = ok(epsSeries(points(1200), { window_end: ago(0) }));
    inFrame(<EpsChart />);
    expect(stale()).toHaveLength(1);
  });

  it("states no time when no step was measured", () => {
    res.byKey[key] = ok(epsSeries([epsPoint(0, null), epsPoint(1, null)]));
    inFrame(<EpsChart />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()).toHaveLength(0);
  });
});

describe("last bucket plus width: the source verdicts (Log source health, Log sources)", () => {
  const BEH = homeReads.sourceBehaviors().key;
  const SUM = homeReads.anomalySummary().key;
  const FLEET = homeReads.fleetSummary("24h").key;
  const OV = homeReads.overview().key;
  const overview = { log_sources: { total_log_sources: 11, in_maintenance: 0 } } as SocOverview;
  const set = (lastBucketAgo: number, widthItem = phaseA()) => {
    res.byKey = {
      [CADENCE]: ok([widthItem, waf()]),
      [BEH]: ok([
        behavior({ log_source_id: "a", last_bucket_at: ago(lastBucketAgo + 3600) }),
        // The newest bucket of any source decides; a silent source's old bucket does not.
        behavior({ log_source_id: "b", last_bucket_at: ago(lastBucketAgo) }),
        behavior({ log_source_id: "c", last_bucket_at: null }),
      ]),
      [SUM]: ok(summary({ monitored_sources: 3 })),
      [FLEET]: ok(fleet([])),
      [OV]: ok(overview),
    };
  };

  it("adds the family's bucket width to the bucket's start", () => {
    // Started 9 min ago, 5 min wide: reaches 4 min ago.
    set(540);
    inFrame(<LogSourceHealth />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()[0]).toHaveTextContent("Data to 11:56:00 UTC, 4 min ago.");
  });

  it("is stale past twice the cadence, in the compact KPI as well", () => {
    set(1200);
    const { unmount } = inFrame(<LogSourceHealth />);
    expect(stale()).toHaveLength(1);
    unmount();
    inFrame(<KpiLogSources />);
    expect(stale()).toHaveLength(1);
  });

  it("uses the bucket's start and the 2 h default when the cadence read is refused", () => {
    set(540);
    res.byKey[CADENCE] = { status: "forbidden", httpStatus: 403 };
    inFrame(<LogSourceHealth />);
    // No width to add, no cadence to judge by: 9 min since the start is fresh by the default.
    expect(stale()).toHaveLength(0);
    expect(dataTo()[0]).toHaveTextContent("Data to 11:51:00 UTC, 9 min ago.");
  });

  it("states no time when no source has a bucket", () => {
    set(0);
    res.byKey[BEH] = ok([behavior({ last_bucket_at: null })]);
    inFrame(<LogSourceHealth />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()).toHaveLength(0);
  });
});

describe("latest bucket with its own width: WAF sources", () => {
  const WAF = homeReads.wafSources().key;
  const withBucket = (startAgo: number) =>
    ok({
      items: [wafSource({ latest_bucket: { ...wafSource().latest_bucket!, bucket_start: ago(startAgo) } })],
      total: 1,
      limit: 200,
      offset: 0,
      range_start: ago(86400),
      range_end: ago(0),
    });

  it("is judged against the WAF family's 15 min cadence", () => {
    // 25 min since the start, 5 min wide: ended 20 min ago, inside 2 x 15 min, past 2 x 5 min.
    res.byKey[WAF] = withBucket(1500);
    inFrame(<WafSources />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()[0]).toHaveTextContent("Data to 11:40:00 UTC, 20 min ago.");
  });

  it("is stale past twice that cadence", () => {
    res.byKey[WAF] = withBucket(2400);
    inFrame(<WafSources />);
    expect(stale()).toHaveLength(1);
  });
});

describe("buckets with their own width: the subject charts", () => {
  const bucket = (startAgo: number) => ({
    bucket_start: ago(startAgo),
    bucket_seconds: 300,
    event_count: 30_000,
    average_eps: 100,
    peak_eps: null,
    completeness: "COMPLETE" as const,
    last_event_at: null,
  });

  it("Log source EPS: the newest bucket's end, against the Phase A cadence", () => {
    const set = (startAgo: number) => {
      res.byKey = {
        ...res.byKey,
        [homeReads.sourceBehaviors().key]: ok([behavior({ log_source_id: SOURCE, name: "fw-01" })]),
        [homeReads.sourceSeries(SOURCE, "24h").key]: ok(series([bucket(startAgo + 300), bucket(startAgo)])),
        [homeReads.sourceCells(SOURCE).key]: ok([epsCell()]),
      };
    };
    set(1200);
    const first = inFrame(<SourceEps config={{ source: SOURCE }} />);
    expect(stale()).toHaveLength(1);
    first.unmount();
    set(600);
    inFrame(<SourceEps config={{ source: SOURCE }} />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()[0]).toHaveTextContent("Data to 11:55:00 UTC, 5 min ago.");
  });

  it("WAF endpoint traffic: the newest bucket's end, against the WAF cadence", () => {
    const req = (startAgo: number) => ({
      ...bucketWaf(),
      bucket_start: ago(startAgo),
    });
    const set = (startAgo: number) => {
      res.byKey = {
        ...res.byKey,
        [homeReads.wafEndpointsOf(SRC).key]: ok({ items: [wafEndpoint({ id: EP })], total: 1, limit: 200, offset: 0 }),
        [homeReads.wafEndpointSeries(EP, "24h").key]: ok(series([req(startAgo)])),
        [homeReads.wafEndpointCells(EP).key]: ok([wafBaselineCell()]),
      };
    };
    set(1500);
    const first = inFrame(<WafEndpointTraffic config={{ waf_source: SRC, endpoint: EP }} />);
    expect(stale()).toHaveLength(0);
    first.unmount();
    set(2400);
    inFrame(<WafEndpointTraffic config={{ waf_source: SRC, endpoint: EP }} />);
    expect(stale()).toHaveLength(1);
  });

  it("a widget that is not configured has no data time", () => {
    inFrame(<SourceEps config={{ source: null }} />);
    expect(stale()).toHaveLength(0);
    expect(dataTo()).toHaveLength(0);
  });
});

describe("no data time in the response: never stale", () => {
  it("EPS by source class draws a month-old answer without a stale note", () => {
    res.byKey[homeReads.fleetSummary("24h").key] = ok(
      fleet([fleetClass("FIREWALL", { mean_eps: 10, events: 100 })], {
        window_start: ago(40 * 86400),
        window_end: ago(30 * 86400),
      }),
      { receivedAt: ago(30 * 86400) },
    );
    inFrame(<EpsByClass />);
    expect(screen.getByRole("link", { name: "Firewall" })).toBeInTheDocument();
    expect(stale()).toHaveLength(0);
    expect(dataTo()).toHaveLength(0);
  });
});

function bucketWaf() {
  return {
    bucket_start: "",
    bucket_seconds: 300,
    request_count: 41,
    request_count_measurement: "EXACT" as const,
    completeness: "COMPLETE" as const,
    partial: false,
    endpoint_coverage_ratio: 0.97,
    in_maintenance: false,
    normalizer_version: 2,
    distinct_client_count: 14,
    dimension: "ENDPOINT",
    measurement: "EXACT",
    coverage_ratio: 1,
    coverage_lower_bound: false,
  };
}
