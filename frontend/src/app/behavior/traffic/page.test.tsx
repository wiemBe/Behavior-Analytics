import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TrafficError from "./error";
import Loading from "./loading";
import TrafficPageView from "./page";
import {
  ApiError,
  api,
  ipsApi,
  type IpsSourceStatus,
  type IpsTrafficItem,
  type PagedWafSources,
  type TrafficPage,
  type WafTrafficItem,
} from "@/lib/api";

const SRC = "11111111-1111-4111-8111-111111111111";
const SRC2 = "22222222-2222-4222-8222-222222222222";

const SERIES: (number | null)[] = [41, 39.8, null, 44.1, 104.2, 40, 41, 42, 43, 41, 40, 39.9];

function wafItem(over: Partial<WafTrafficItem> = {}): WafTrafficItem {
  return {
    id: "e-1",
    virtual_host: "api.example.com",
    http_method: "GET",
    normalized_endpoint: "/v1/orders/{id}",
    events: 148320,
    rate_per_second: 41.2,
    peak_rate_per_second: 77.9,
    peak_bucket_start: "2026-11-05T18:35:00Z",
    buckets_with_data: 11,
    partial_buckets: 0,
    active: true,
    series: SERIES,
    ...over,
  };
}

function ipsItem(over: Partial<IpsTrafficItem> = {}): IpsTrafficItem {
  return {
    id: "i-1",
    destination_ip: "10.20.30.40",
    display_name: "dmz web",
    events: 9000,
    rate_per_second: 12.5,
    peak_rate_per_second: 20,
    peak_bucket_start: "2026-11-05T18:40:00Z",
    buckets_with_data: 12,
    partial_buckets: 0,
    active: true,
    series: SERIES,
    ...over,
  };
}

function page<T>(items: T[], over: Partial<TrafficPage<T>> = {}): TrafficPage<T> {
  return {
    window: "1h",
    range_start: "2026-11-05T18:00:00Z",
    range_end: "2026-11-05T19:00:00Z",
    bucket_seconds: 300,
    buckets_expected: 12,
    buckets_measured: 12,
    state: "OK",
    reason_code: null,
    source_total_events: 812345,
    attributed_events: 790001,
    unattributed_events: 22344,
    max_rate_per_second: 41.2,
    series_start: "2026-11-05T18:00:00Z",
    series_step_seconds: 300,
    series_points: 12,
    total: items.length,
    limit: 10,
    offset: 0,
    sort: "rate",
    order: "desc",
    items,
    ...over,
  };
}

function wafSources(): PagedWafSources {
  const base = {
    product_label: null,
    enabled: true,
    profile_version: 1,
    validation_status: "VALID",
    validated_profile_version: 1,
    validated_at: null,
    validation_current: true,
    virtual_host_available: true,
    latest_bucket: null,
    active_endpoints: 1,
    total_endpoints: 1,
    active_endpoint_ceiling: 200,
    admission: {
      buckets_in_range: 0,
      buckets_admission_truncated: null,
      rejected_over_quota: null,
      below_admission_threshold: null,
      untracked_requests: null,
    },
  };
  return {
    items: [
      { ...base, log_source_id: SRC, log_source_name: "WAF one" },
      { ...base, log_source_id: SRC2, log_source_name: "WAF two" },
    ],
    total: 2,
    limit: 100,
    offset: 0,
    range_start: "2026-11-04T00:00:00Z",
    range_end: "2026-11-05T00:00:00Z",
  };
}

function setup(waf: TrafficPage<WafTrafficItem> | Error, ips?: TrafficPage<IpsTrafficItem> | Error) {
  vi.spyOn(api, "wafBehaviorSources").mockResolvedValue(wafSources());
  vi.spyOn(ipsApi, "sources").mockResolvedValue([
    { log_source_id: SRC, log_source_name: "IPS one", enabled: true, collection_eligible: true } as IpsSourceStatus,
  ]);
  const wafCall = vi
    .spyOn(api, "wafTraffic")
    .mockImplementation(async () => (waf instanceof Error ? Promise.reject(waf) : waf));
  const ipsCall = vi
    .spyOn(api, "ipsTraffic")
    .mockImplementation(async () => (ips instanceof Error ? Promise.reject(ips) : (ips ?? page([]))));
  return { wafCall, ipsCall };
}

async function renderPage(params: Record<string, string | undefined> = {}) {
  return render(await TrafficPageView({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Traffic by destination: the ranked table", () => {
  it("has one h1 and asks for the first source with the default query", async () => {
    const { wafCall } = setup(page([wafItem()]));
    const { container } = await renderPage();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Traffic by destination");
    expect(wafCall).toHaveBeenCalledWith(SRC, {
      window: "1h",
      sort: "rate",
      order: "desc",
      limit: 10,
      offset: 0,
    });
  });

  it("shows host, method and path, the rate with its unit, the peak with its time and the counts", async () => {
    setup(page([wafItem()]));
    await renderPage();
    const table = screen.getByRole("table", { name: /WAF endpoints ranked/ });
    const row = within(table).getAllByRole("row")[1];
    expect(within(row).getByText("api.example.com")).toBeInTheDocument();
    expect(row).toHaveTextContent("GET /v1/orders/{id}");
    expect(row).toHaveTextContent("41.2");
    expect(screen.getByRole("columnheader", { name: /Average \(events\/s\)/ })).toBeInTheDocument();
    expect(row).toHaveTextContent("77.9");
    expect(screen.getByRole("columnheader", { name: /Peak \(events\/s\)/ })).toBeInTheDocument();
    expect(row.querySelector("time")).toHaveAttribute("datetime", "2026-11-05T18:35:00Z");
    expect(row).not.toHaveTextContent("148,320");
    expect(screen.queryByRole("columnheader", { name: /Events/ })).toBeNull();
    // 11 of 12 buckets have data on a 12-of-12 measured page: the row says so
    expect(row).toHaveTextContent("data in 11 of 12 buckets");
  });

  it("states the range in UTC, the unit and the measured buckets", async () => {
    setup(page([wafItem()], { buckets_measured: 11 }));
    await renderPage();
    const text = document.body.textContent ?? "";
    expect(text).toContain("2026-11-05 18:00:00 UTC");
    expect(text).toContain("2026-11-05 19:00:00 UTC");
    expect(text).toContain("Average events/s over 5-minute buckets");
    expect(text).toContain("11 of 12 buckets measured");
    expect(text).not.toMatch(/real-time|\blive\b/i);
  });

  it("draws a trend per row with a gap, scaled to the row, and a text alternative", async () => {
    setup(page([wafItem(), wafItem({ id: "b", series: [1000, 2000, 1500, 1200, 900, 1100, 1300, 1000, 1000, 1000, 1000, 1000] })]));
    const { container } = await renderPage();
    const sparks = container.querySelectorAll("svg.traffic-spark");
    expect(sparks).toHaveLength(2);
    expect(sparks[0].getAttribute("role")).toBe("img");
    expect(sparks[0].getAttribute("aria-label")).toBe(
      "events/s from 11-05 18:00 UTC to 11-05 19:00 UTC, low 39.8, high 104.2, 1 gap",
    );
    // the null splits the line in two; it is not drawn as a zero
    expect(sparks[0].querySelectorAll("polyline")).toHaveLength(2);
    expect(screen.getByRole("columnheader", { name: /Trend/ })).toHaveTextContent("scaled per row");
  });

  it("says a row with no measured point has no measured data", async () => {
    setup(page([wafItem({ series: Array(12).fill(null) })]));
    const { container } = await renderPage();
    expect(container.querySelector("svg.traffic-spark")).toBeNull();
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("no measured data");
  });

  it("draws no trend for the 5 min window and the header says why", async () => {
    setup(page([wafItem({ series: [41] })], { window: "5m", series_points: 1 }));
    const { container } = await renderPage({ window: "5m" });
    expect(container.querySelector("svg.traffic-spark")).toBeNull();
    expect(screen.getByRole("columnheader", { name: /Trend/ })).toHaveTextContent("one bucket");
  });

  it("describes a reason code in a sentence and never prints an unknown code", async () => {
    setup(page([wafItem()], { state: "PARTIAL", reason_code: "waf_traffic_detail.reason.buckets_missing", buckets_measured: 11 }));
    await renderPage();
    expect(document.body.textContent).toContain("were not collected");
    expect(document.body.textContent).not.toContain("waf_traffic_detail");
    expect(document.body.textContent).not.toContain("buckets_missing");
  });

  it.each([
    ["waf_traffic_detail.reason.bucket_partial", "A measured bucket in this window is partial, so its endpoint counts are lower bounds."],
    ["waf_traffic_detail.reason.buckets_missing", "Some buckets of this window were not collected, so the rates cover the measured buckets only."],
    ["waf_traffic_detail.reason.no_complete_bucket", "No complete bucket has been stored for this WAF source, so no rate can be measured."],
    ["waf_traffic_detail.reason.no_usable_bucket", "No bucket in this window has a usable endpoint measurement, so no rate can be measured."],
    ["waf_traffic_detail.reason.not_collected", "This WAF source has no enabled, validated profile, so no traffic was collected."],
    ["waf_traffic_detail.reason.truncated", "A bucket in this window lost endpoint groups or new endpoints, so the ranking is a lower bound."],
    ["ips_traffic_detail.reason.bucket_partial", "A measured bucket in this window is a lower bound, so its destination-IP counts are lower bounds."],
    ["ips_traffic_detail.reason.buckets_missing", "Some buckets of this window were not collected, so the rates cover the measured buckets only."],
    ["ips_traffic_detail.reason.no_complete_bucket", "No bucket has been stored for this IPS source, so no rate can be measured."],
    ["ips_traffic_detail.reason.no_usable_bucket", "No bucket in this window has a usable destination-IP measurement, so no rate can be measured."],
    ["ips_traffic_detail.reason.not_collected", "This IPS source has no enabled, validated profile, so no traffic was collected."],
    ["ips_traffic_detail.reason.truncated", "A bucket in this window lost destination-IP entities, so the ranking is a lower bound."],
  ] as const)("reason code %s is shown as its backend sentence", async (code, sentence) => {
    setup(page([], { state: "INSUFFICIENT_DATA", reason_code: code }));
    await renderPage();
    expect(document.body.textContent).toContain(`Reason: ${sentence}`);
    expect(document.body.textContent).not.toContain(code);
  });

  it("renders a NOT_COLLECTED answer with no window width, no grid step and no dates", async () => {
    setup(
      page([], {
        state: "NOT_COLLECTED",
        reason_code: "waf_traffic_detail.reason.not_collected",
        range_start: null,
        range_end: null,
        bucket_seconds: null,
        series_start: null,
        series_step_seconds: null,
        series_points: 0,
        source_total_events: null,
        attributed_events: null,
        unattributed_events: null,
        max_rate_per_second: null,
        buckets_expected: 0,
        buckets_measured: 0,
      }),
    );
    await renderPage();
    expect(document.body.textContent).toContain("no enabled, validated profile");
    expect(document.body.textContent).not.toMatch(/NaN|undefined/);
  });

  it("draws a row whose endpoint and method are null without throwing", async () => {
    setup(page([wafItem({ http_method: null, normalized_endpoint: null })]));
    await renderPage();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("an unknown reason code gets the generic sentence, not the code", async () => {
    setup(page([], { state: "INSUFFICIENT_DATA", reason_code: "brand_new_code_x" }));
    await renderPage();
    expect(document.body.textContent).toContain("does not describe");
    expect(document.body.textContent).not.toContain("brand_new_code_x");
  });

  it("shows no coverage marker when the row has data in every measured bucket", async () => {
    setup(page([wafItem({ buckets_with_data: 12 })]));
    await renderPage();
    expect(screen.getAllByRole("row")[1]).not.toHaveTextContent("data in");
  });

  it("marks a retired row and a row with partial buckets in words", async () => {
    setup(page([wafItem({ active: false, partial_buckets: 2 })]));
    await renderPage();
    const row = screen.getAllByRole("row")[1];
    expect(row).toHaveTextContent("Retired");
    expect(row).toHaveTextContent("2 partial buckets");
  });

  it("shows a missing host as such and links an endpoint to its source's endpoint list", async () => {
    setup(page([wafItem({ virtual_host: null })]));
    await renderPage({ source: SRC2 });
    expect(screen.getByText("no host")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /GET/ })).toHaveAttribute(
      "href",
      `/behavior/waf/sources/${SRC2}`,
    );
  });
});

describe("Traffic by destination: sorting, paging and controls", () => {
  it("marks the sorted column with aria-sort and links the others to their own sort", async () => {
    setup(page([wafItem()]));
    await renderPage();
    const rate = screen.getByRole("columnheader", { name: /Average \(events\/s\)/ });
    expect(rate).toHaveAttribute("aria-sort", "descending");
    expect(within(rate).getByRole("link")).toHaveAttribute("href", "/behavior/traffic?order=asc");
    const host = screen.getByRole("columnheader", { name: "Host" });
    expect(host).toHaveAttribute("aria-sort", "none");
    expect(within(host).getByRole("link")).toHaveAttribute("href", "/behavior/traffic?sort=host");
  });

  it("sends the sort to the backend and reflects it", async () => {
    const { wafCall } = setup(page([wafItem()], { sort: "host", order: "asc" }));
    await renderPage({ sort: "host", order: "asc", offset: "20", limit: "25" });
    expect(wafCall).toHaveBeenCalledWith(SRC, {
      window: "1h",
      sort: "host",
      order: "asc",
      limit: 25,
      offset: 20,
    });
    expect(screen.getByRole("columnheader", { name: "Host" })).toHaveAttribute("aria-sort", "ascending");
    expect(within(screen.getByRole("columnheader", { name: "Host" })).getByRole("link")).toHaveAttribute(
      "href",
      "/behavior/traffic?sort=host&order=desc&limit=25",
    );
  });

  it("a page size link resets the offset and keeps the rest", async () => {
    setup(page([wafItem()], { total: 137, offset: 20 }));
    await renderPage({ window: "7d", sort: "peak", offset: "20" });
    const size = within(screen.getByRole("navigation", { name: "Rows per page" }));
    expect(size.getByRole("link", { name: "50" })).toHaveAttribute(
      "href",
      "/behavior/traffic?window=7d&sort=peak&limit=50",
    );
    expect(size.getByRole("link", { name: "10" })).toHaveAttribute("aria-current", "page");
  });

  it("a window link resets the offset, and every window is offered", async () => {
    setup(page([wafItem()], { total: 137 }));
    await renderPage({ offset: "30" });
    const nav = within(screen.getByRole("navigation", { name: "Window" }));
    expect(nav.getAllByRole("link").map((a) => a.textContent)).toEqual([
      "5 min",
      "1 hour",
      "24 hours",
      "7 days",
      "28 days",
    ]);
    expect(nav.getByRole("link", { name: "28 days" })).toHaveAttribute(
      "href",
      "/behavior/traffic?window=28d",
    );
    expect(nav.getByRole("link", { name: "1 hour" })).toHaveAttribute("aria-current", "page");
  });

  it("asks for the window in the URL", async () => {
    const { wafCall } = setup(page([wafItem()], { window: "28d" }));
    await renderPage({ window: "28d" });
    expect(wafCall.mock.calls[0][1]).toMatchObject({ window: "28d" });
  });

  it("numbers rows by their position in the whole list and pages with the range", async () => {
    setup(page([wafItem()], { total: 137, offset: 10 }));
    await renderPage({ offset: "10" });
    expect(screen.getAllByRole("row")[1]).toHaveTextContent(/^11/);
    expect(screen.getByText("11–20 of 137")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      "/behavior/traffic?offset=20",
    );
  });

  it("offers a source choice when there is more than one and keeps the other settings", async () => {
    const { wafCall } = setup(page([wafItem()]));
    await renderPage({ source: SRC2, window: "24h" });
    expect(wafCall.mock.calls[0][0]).toBe(SRC2);
    const nav = within(screen.getByRole("navigation", { name: "Log source" }));
    expect(nav.getByRole("link", { name: "WAF two" })).toHaveAttribute("aria-current", "page");
    expect(nav.getByRole("link", { name: "WAF one" })).toHaveAttribute(
      "href",
      `/behavior/traffic?source=${SRC}&window=24h`,
    );
  });
});

describe("Traffic by destination: the IPS view", () => {
  it("lists destination IPs from the IPS route and links a row to its anomalies", async () => {
    const { wafCall, ipsCall } = setup(page([wafItem()]), page([ipsItem()]));
    await renderPage({ view: "ips" });
    expect(wafCall).not.toHaveBeenCalled();
    expect(ipsCall).toHaveBeenCalledWith(SRC, expect.objectContaining({ window: "1h" }));
    const table = screen.getByRole("table", { name: /IPS destination IPs ranked/ });
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("10.20.30.40");
    expect(row).toHaveTextContent("dmz web");
    expect(row).toHaveTextContent("12.5");
    expect(within(row).getByRole("link", { name: "10.20.30.40" })).toHaveAttribute(
      "href",
      "/anomalies?subject_kind=IPS_ENTITY&ips_entity_id=i-1",
    );
    expect(screen.getByRole("columnheader", { name: "Destination IP" })).toHaveAttribute("aria-sort", "none");
    expect(screen.queryByRole("columnheader", { name: "Host" })).toBeNull();
    expect(screen.getByRole("link", { name: "IPS destination IPs" })).toHaveAttribute("aria-current", "page");
  });
});

describe("Traffic by destination: honesty states", () => {
  it("PARTIAL puts the missing buckets above the table and the unattributed share beside it", async () => {
    setup(
      page([wafItem({ partial_buckets: 1 })], {
        state: "PARTIAL",
        reason_code: "waf_traffic_detail.reason.buckets_missing",
        buckets_measured: 11,
      }),
    );
    await renderPage();
    const banner = screen.getByText("Partial data.").closest(".banner")!;
    expect(banner).toHaveTextContent("Only 11 of 12 buckets");
    expect(banner).toHaveTextContent("not counted as zero");
    expect(banner.compareDocumentPosition(screen.getByRole("table"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(document.body.textContent).toMatch(
      /22,344 of 812,345 events \(2\.8\s%\) are not attributed to a listed endpoint/,
    );
    expect(document.body.textContent).toContain("lower bound");
  });

  it("PARTIAL with every bucket measured says some buckets were incomplete", async () => {
    setup(page([wafItem({ partial_buckets: 2 })], { state: "PARTIAL" }));
    await renderPage();
    const banner = screen.getByText("Partial data.").closest(".banner")!;
    expect(banner).toHaveTextContent("Some measured buckets were incomplete");
    expect(banner).not.toHaveTextContent("Only");
  });

  it("TRUNCATED says figures are lower bounds", async () => {
    setup(page([wafItem()], { state: "TRUNCATED" }));
    await renderPage();
    expect(screen.getByText("Truncated.").closest(".banner")).toHaveTextContent("lower bounds");
  });

  it("an unknown unattributed share is said to be unknown, not drawn as 0", async () => {
    setup(page([wafItem()], { unattributed_events: null }));
    await renderPage();
    expect(document.body.textContent).toContain("is not known for this window");
    expect(document.body.textContent).not.toContain("0 of");
  });

  it("INSUFFICIENT_DATA ranks nothing and says it is not zero traffic", async () => {
    setup(
      page([], {
        state: "INSUFFICIENT_DATA",
        reason_code: "waf_traffic_detail.reason.no_complete_bucket",
        range_end: null,
        max_rate_per_second: null,
        source_total_events: null,
        unattributed_events: null,
        buckets_measured: 0,
      }),
    );
    await renderPage();
    expect(screen.getByText("No complete bucket in this window.")).toBeInTheDocument();
    expect(screen.getByText(/not a statement that no traffic arrived/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("NOT_COLLECTED links to the admin page that sets the source up", async () => {
    setup(page([], { state: "NOT_COLLECTED", reason_code: "waf_traffic_detail.reason.not_collected" }));
    await renderPage();
    expect(screen.getByText("This WAF source is not collected.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "WAF sources" })).toHaveAttribute("href", "/admin/waf-sources");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("NOT_COLLECTED on the IPS view links to IPS sources", async () => {
    setup(page([]), page([], { state: "NOT_COLLECTED" }));
    await renderPage({ view: "ips" });
    expect(screen.getByRole("link", { name: "IPS sources" })).toHaveAttribute("href", "/admin/ips-sources");
  });

  it("an empty window says what is empty and that it is not zero requests", async () => {
    setup(page([], { total: 0, max_rate_per_second: null }));
    await renderPage();
    expect(screen.getByText("No endpoint has data in this window.")).toBeInTheDocument();
    expect(screen.getByText(/not zero requests/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("an offset past the end offers the first page", async () => {
    setup(page([], { total: 37, offset: 100 }));
    await renderPage({ offset: "100" });
    expect(screen.getByText("This page is past the end of the list.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to the first page" })).toHaveAttribute(
      "href",
      "/behavior/traffic",
    );
  });

  it("forbidden names the permission", async () => {
    setup(new ApiError(403, "forbidden"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("read:anomalies");
  });

  it("a failed read says what failed and what to do", async () => {
    setup(new ApiError(500, "boom"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Traffic could not be shown.");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("an unknown source (404) says the source is not known", async () => {
    setup(new ApiError(404, "not found"));
    await renderPage({ source: "nope" });
    expect(screen.getByRole("alert")).toHaveTextContent("not known");
  });

  it("a source list that cannot be read, with no source in the URL, is an error", async () => {
    setup(page([wafItem()]));
    vi.spyOn(api, "wafBehaviorSources").mockRejectedValue(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("list of sources could not be loaded");
  });

  it("no source at all says nothing is collected and links the admin page", async () => {
    setup(page([]));
    vi.spyOn(api, "wafBehaviorSources").mockResolvedValue({ ...wafSources(), items: [], total: 0 });
    const { wafCall } = { wafCall: vi.mocked(api.wafTraffic) };
    await renderPage();
    expect(wafCall).not.toHaveBeenCalled();
    expect(screen.getByText("No WAF source has a behavior profile.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "WAF sources" })).toHaveAttribute("href", "/admin/waf-sources");
  });
});

describe("Traffic by destination: route boundaries", () => {
  it("loading names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("traffic by destination");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Traffic by destination");
  });

  it("error offers a retry and does not print the exception", () => {
    const retry = vi.fn();
    render(<TrafficError error={Object.assign(new Error("secret stack"), { digest: "d1" })} reset={retry} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("secret stack");
  });
});

describe("Traffic by destination: 5 min", () => {
  it("says it is the newest complete bucket and shows its time", async () => {
    setup(
      page([wafItem()], {
        window: "5m",
        range_start: "2026-11-05T18:55:00Z",
        range_end: "2026-11-05T19:00:00Z",
        buckets_expected: 1,
        buckets_measured: 1,
      }),
    );
    await renderPage({ window: "5m" });
    const text = document.body.textContent ?? "";
    expect(text).toContain("Newest complete bucket: 2026-11-05 18:55:00 UTC to 2026-11-05 19:00:00 UTC");
  });
});

describe("Traffic by destination: export", () => {
  it("offers the export with the page's total and window", async () => {
    setup(page([wafItem()], { total: 80 }));
    await renderPage({ window: "7d" });
    expect(screen.getByRole("link", { name: "Export CSV (all 80 endpoints, 7 days)" })).toBeInTheDocument();
  });

  it("disables the export after a failed read", async () => {
    setup(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.queryByRole("link", { name: /Export CSV/ })).toBeNull();
    expect(screen.getAllByText(/could not be loaded/).length).toBeGreaterThan(0);
  });
});

describe("Traffic by destination: All rows", () => {
  it("asks for 1000 rows from offset 0 and shows no paging", async () => {
    const { wafCall } = setup(page([wafItem()], { total: 137, limit: 1000 }));
    await renderPage({ limit: "all", offset: "30" });
    expect(wafCall.mock.calls[0][1]).toMatchObject({ limit: 1000, offset: 0 });
    expect(screen.queryByRole("navigation", { name: "Pagination" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Next" })).toBeNull();
    expect(document.body.textContent).not.toContain("Showing the first");
    const size = within(screen.getByRole("navigation", { name: "Rows per page" }));
    expect(size.getByRole("link", { name: "All" })).toHaveAttribute("aria-current", "page");
  });

  it("says the list is cut only when the total exceeds 1000", async () => {
    setup(page([wafItem()], { total: 1500, limit: 1000 }));
    await renderPage({ limit: "all" });
    expect(document.body.textContent).toContain(
      "Showing the first 1,000 of 1,500 (sorted). Export CSV has the full list.",
    );
  });

  it("does not say it at exactly 1000", async () => {
    setup(page([wafItem()], { total: 1000, limit: 1000 }));
    await renderPage({ limit: "all" });
    expect(document.body.textContent).not.toContain("Showing the first");
  });

  it("the All link resets the offset and keeps the rest", async () => {
    setup(page([wafItem()], { total: 137, offset: 20 }));
    await renderPage({ window: "7d", offset: "20" });
    const size = within(screen.getByRole("navigation", { name: "Rows per page" }));
    expect(size.getByRole("link", { name: "All" })).toHaveAttribute(
      "href",
      "/behavior/traffic?window=7d&limit=all",
    );
  });

  it("renders 1000 rows with a trend each", async () => {
    const items = Array.from({ length: 1000 }, (_, i) => wafItem({ id: `e-${i}` }));
    setup(page(items, { total: 1000, limit: 1000 }));
    const { container } = await renderPage({ limit: "all" });
    expect(container.querySelectorAll("tbody tr")).toHaveLength(1000);
    expect(container.querySelectorAll("svg.traffic-spark")).toHaveLength(1000);
  });
});
