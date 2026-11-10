import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IPS_BEHAVIOR_SOURCE_ID, ipsEntity, ipsSource } from "@/components/ips/behaviorFixtures";
import { ipsCell, ipsSourceBucket } from "@/components/ips/fixtures";
import {
  ApiError,
  api,
  type IpsBaselineCell,
  type IpsEntityInventory,
  type IpsSourceBehavior,
  type IpsSourceMetricBucket,
} from "@/lib/api";

import IpsSourceError from "./error";
import Loading from "./loading";
import IpsSourcePage from "./page";

const chart = vi.hoisted(() => ({ last: null as null | Record<string, unknown> }));
vi.mock("@/components/ips/IpsSeriesChart", () => ({
  IpsSeriesChart: (props: Record<string, unknown>) => {
    chart.last = props;
    return (
      <>
        <div role="img" aria-label={String(props.ariaLabel)} />
        <p>{String(props.textSummary ?? "")}</p>
      </>
    );
  },
}));

const ID = IPS_BEHAVIOR_SOURCE_ID;
const at = (minute: number) => `2026-10-12T09:${String(minute).padStart(2, "0")}:00Z`;

beforeEach(() => {
  vi.restoreAllMocks();
  chart.last = null;
});

interface Setup {
  entities?: IpsEntityInventory[];
  total?: number;
  source?: IpsSourceBehavior | null;
  buckets?: IpsSourceMetricBucket[];
  metricsTotal?: number;
  cells?: IpsBaselineCell[];
  search?: Record<string, string | undefined>;
}

async function renderPage(o: Setup = {}) {
  const entities = o.entities ?? [ipsEntity()];
  const buckets = o.buckets ?? [ipsSourceBucket({ bucket_start: at(0) }), ipsSourceBucket({ bucket_start: at(5) })];
  const source = o.source === undefined ? ipsSource() : o.source;
  vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue({
    items: source ? [source] : [],
    total: source ? 1 : 0,
    limit: 200,
    offset: 0,
  });
  const entityCall = vi
    .spyOn(api, "ipsBehaviorEntities")
    .mockResolvedValue({ items: entities, total: o.total ?? entities.length, limit: 50, offset: 0 });
  const metricsCall = vi.spyOn(api, "ipsSourceMetrics").mockResolvedValue({
    items: buckets,
    total: o.metricsTotal ?? buckets.length,
    limit: 5000,
    offset: 0,
    range_start: "2026-10-12T08:00:00Z",
    range_end: "2026-10-13T08:00:00Z",
  });
  const baselineCall = vi
    .spyOn(api, "ipsSourceBaselines")
    .mockResolvedValue({ items: o.cells ?? [], total: (o.cells ?? []).length, limit: 200, offset: 0 });
  const view = render(
    await IpsSourcePage({
      params: Promise.resolve({ id: ID }),
      searchParams: Promise.resolve(o.search ?? {}),
    }),
  );
  return { ...view, entityCall, metricsCall, baselineCall };
}

describe("IPS source: header", () => {
  it("names the source as the single h1 with product, validation, profile and latest bucket", async () => {
    const { container } = await renderPage();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("lab-ips-01");
    expect(screen.getByText("Cisco Firepower Threat Defense")).toBeInTheDocument();
    expect(screen.getByText("VALID")).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByText(/2026-10-13 08:55:00 UTC/)).toBeInTheDocument();
  });

  it("links the list, the administration page and Traffic with this source in the IPS view", async () => {
    await renderPage();
    expect(screen.getByRole("link", { name: "All IPS sources" })).toHaveAttribute("href", "/behavior/ips");
    expect(screen.getByRole("link", { name: "Profile settings" })).toHaveAttribute(
      "href",
      `/admin/ips-sources/${ID}`,
    );
    expect(screen.getByRole("link", { name: "Traffic" })).toHaveAttribute(
      "href",
      `/behavior/traffic?view=ips&source=${ID}`,
    );
  });

  it("says a source without a bucket has none, and a source that is not collected is not judged", async () => {
    await renderPage({
      source: ipsSource({ latest_bucket: null, enabled: false, validation_current: false }),
    });
    expect(screen.getByText("No bucket collected yet")).toBeInTheDocument();
    expect(screen.getByText(/This source is not collected/)).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
  });

  it("does not say the source is not collected when it is judged", async () => {
    await renderPage();
    expect(screen.queryByText(/This source is not collected:/)).toBeNull();
  });
});

describe("IPS source: the chart", () => {
  it("draws total events by default, with no expected range and a sentence saying why", async () => {
    const { baselineCall, metricsCall } = await renderPage();
    expect(chart.last).toMatchObject({ unit: "events", quantity: "Total events", baseline: null });
    expect(baselineCall).not.toHaveBeenCalled();
    expect(screen.getByText(/No expected range is drawn for total events/)).toBeInTheDocument();
    const { since, until } = metricsCall.mock.calls[0][1]!;
    expect((Date.parse(until!) - Date.parse(since!)) / 3600_000).toBeCloseTo(24, 5);
    expect(screen.getByRole("link", { name: "24 hours" })).toHaveAttribute("aria-current", "page");
  });

  it("hands the chart a gap for an interval with no stored bucket, never a zero", async () => {
    await renderPage({
      buckets: [ipsSourceBucket({ bucket_start: at(0) }), ipsSourceBucket({ bucket_start: at(30) })],
    });
    const points = chart.last!.points as Array<{ kind: string; observed: number | null }>;
    expect(points.map((p) => p.kind)).toEqual(["COMPLETE", "GAP", "COMPLETE"]);
    expect(points[1].observed).toBeNull();
    expect(screen.getByText(/1 intervals had no stored bucket and are drawn as gaps/)).toBeInTheDocument();
  });

  it("passes a band source only when a reliable cell exists for the series' metric", async () => {
    await renderPage({
      search: { series: "fanout" },
      cells: [ipsCell({ metric_name: "max_distinct_ports", is_reliable: true })],
    });
    expect(chart.last).toMatchObject({
      unit: "ports",
      baseline: { metric: "max_distinct_ports", pooled: false },
    });
    expect(screen.queryByText(/No seasonal cell of this series is reliable yet/)).toBeNull();
  });

  it("draws no band and says so when no cell is reliable", async () => {
    await renderPage({
      search: { series: "fanout" },
      cells: [ipsCell({ metric_name: "max_distinct_ports", is_reliable: false })],
    });
    expect(chart.last).toMatchObject({ baseline: null });
    expect(screen.getByText(/No seasonal cell of this series is reliable yet/)).toBeInTheDocument();
    expect(screen.getByText("INSUFFICIENT_DATA.")).toBeInTheDocument();
  });

  it("treats a share series as pooled and asks only for its own metric", async () => {
    const { baselineCall } = await renderPage({
      search: { series: "block_ratio" },
      cells: [ipsCell({ metric_name: "block_ratio" })],
    });
    expect(baselineCall).toHaveBeenCalledWith(ID, { metric: "block_ratio", limit: 200 });
    expect(chart.last).toMatchObject({ unit: "share", baseline: { pooled: true } });
  });

  it("says the band is missing, not normal, when the cells could not be loaded", async () => {
    vi.spyOn(api, "ipsSourceBaselines").mockRejectedValue(new Error("down"));
    vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue({ items: [ipsSource()], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "ipsBehaviorEntities").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    vi.spyOn(api, "ipsSourceMetrics").mockResolvedValue({
      items: [ipsSourceBucket()],
      total: 1,
      limit: 5000,
      offset: 0,
      range_start: "2026-10-12T08:00:00Z",
      range_end: "2026-10-13T08:00:00Z",
    });
    render(
      await IpsSourcePage({
        params: Promise.resolve({ id: ID }),
        searchParams: Promise.resolve({ series: "fanout" }),
      }),
    );
    expect(screen.getByText(/could not be loaded, so no expected range is drawn/)).toBeInTheDocument();
    expect(chart.last).toMatchObject({ baseline: null });
  });

  it("marks buckets that were not measured exactly as PARTIAL", async () => {
    await renderPage({
      search: { series: "concentration" },
      buckets: [
        ipsSourceBucket({
          dimensions: [
            { dimension: "SOURCE_IP", measurement: "LOWER_BOUND", coverage_ratio: 0.6, coverage_lower_bound: true },
          ],
        }),
      ],
    });
    expect(screen.getByText("PARTIAL.")).toBeInTheDocument();
    expect(screen.getByText(/1 of 1 stored buckets were not measured exactly/)).toBeInTheDocument();
  });

  it("states a range with more buckets than were drawn", async () => {
    await renderPage({ metricsTotal: 9000 });
    expect(screen.getByText(/Only 2 of 9,000 buckets in this range are drawn/)).toBeInTheDocument();
  });

  it("asks for 7 days when the range says so, and falls back to 24 hours for a range it does not know", async () => {
    const week = await renderPage({ search: { range: "7d" } });
    const { since, until } = week.metricsCall.mock.calls[0][1]!;
    expect((Date.parse(until!) - Date.parse(since!)) / 86400_000).toBeCloseTo(7, 5);
    expect(screen.getByRole("link", { name: "7 days" })).toHaveAttribute("aria-current", "page");
    week.unmount();
    const odd = await renderPage({ search: { range: "forever" } });
    const call = odd.metricsCall.mock.calls.at(-1)![1]!;
    expect((Date.parse(call.until!) - Date.parse(call.since!)) / 3600_000).toBeCloseTo(24, 5);
  });

  it("says no bucket was stored, not zero events, for an empty range", async () => {
    await renderPage({ buckets: [] });
    expect(screen.getByRole("status", { name: "" })).toBeTruthy();
    expect(screen.getAllByText(/absence of collection, not an observation of zero events/)).toHaveLength(2);
    expect(screen.queryByText(/Action not mapped/)).toBeNull();
    expect(chart.last).toBeNull();
  });

  it("gives the chart a text summary and an accessible name", async () => {
    await renderPage();
    expect(screen.getByRole("img", { name: "Total events against the expected range" })).toBeInTheDocument();
    expect(screen.getByText(/Total events for lab-ips-01 across 2 stored buckets/)).toBeInTheDocument();
  });

  it("keeps a range choice across a series choice and the reverse", async () => {
    await renderPage({ search: { range: "7d", series: "fanout" } });
    expect(screen.getByRole("link", { name: "Port fan-out" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Total events" })).toHaveAttribute(
      "href",
      `/behavior/ips/sources/${ID}?range=7d`,
    );
    expect(screen.getByRole("link", { name: "24 hours" })).toHaveAttribute(
      "href",
      `/behavior/ips/sources/${ID}?series=fanout`,
    );
  });
});

describe("IPS source: action mix", () => {
  it("shows the sums and shares of the range when the action is mapped", async () => {
    await renderPage({
      buckets: [
        ipsSourceBucket({ bucket_start: at(0), blocked_events: 300, allowed_events: 600, unmapped_events: 100 }),
      ],
    });
    const table = within(screen.getByRole("table", { name: /Events by action/ }));
    expect(table.getByText("Blocked").closest("tr")).toHaveTextContent("300");
    expect(table.getByText("Blocked").closest("tr")).toHaveTextContent("30.0");
    expect(table.getByText("Allowed").closest("tr")).toHaveTextContent("600");
    expect(screen.getByText(/1 of 1 buckets in the range carried an action split/)).toBeInTheDocument();
  });

  it("says the action is not mapped, and draws no zeros, when the fields are null", async () => {
    await renderPage({
      buckets: [
        ipsSourceBucket({ bucket_start: at(0), blocked_events: null, allowed_events: null, unmapped_events: null }),
      ],
    });
    expect(screen.getByText(/Action not mapped on this source/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Events by action/ })).toBeNull();
  });
});

describe("IPS source: entities", () => {
  it("lists entities with the dimension filter and keeps the filter in the pagination", async () => {
    const { entityCall } = await renderPage({
      entities: [ipsEntity()],
      total: 120,
      search: { dimension: "SIGNATURE" },
    });
    expect(entityCall).toHaveBeenCalledWith(ID, { dimension: "SIGNATURE", limit: 50, offset: 0 });
    expect(screen.getByRole("link", { name: "Signature" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "All" })).toHaveAttribute("href", `/behavior/ips/sources/${ID}`);
    expect(screen.getByRole("link", { name: "Next" }).getAttribute("href")).toContain("dimension=SIGNATURE");
  });

  it("ignores a dimension it does not know and sends no filter", async () => {
    const { entityCall } = await renderPage({ search: { dimension: "SOURCE_IP" } });
    expect(entityCall).toHaveBeenCalledWith(ID, { dimension: undefined, limit: 50, offset: 0 });
  });

  it("pages the inventory at 50", async () => {
    const { entityCall } = await renderPage({ total: 120, search: { offset: "50" } });
    expect(entityCall).toHaveBeenCalledWith(ID, { dimension: undefined, limit: 50, offset: 50 });
  });

  it("lists a retired entity and a never-evaluated one, each in its own words", async () => {
    await renderPage({
      entities: [
        ipsEntity({ id: "r", value_key: "1/tcp", active: false, retired_at: "2026-10-05T00:00:00Z" }),
        ipsEntity({ id: "n", value_key: "2/tcp", state: null }),
      ],
    });
    expect(within(screen.getByText("Destination port 1/tcp").closest("tr")!).getByText("Retired")).toBeInTheDocument();
    const fresh = within(screen.getByText("Destination port 2/tcp").closest("tr")!);
    expect(fresh.getByText("Not evaluated")).toBeInTheDocument();
    expect(fresh.queryByText("NORMAL")).toBeNull();
  });

  it("says what is empty and why: a collected source with no entity yet", async () => {
    await renderPage({ entities: [] });
    expect(screen.getByText("No entity is tracked for this source.")).toBeInTheDocument();
    expect(screen.getByText(/None has been admitted yet, so no entity is being judged/)).toBeInTheDocument();
  });

  it("says an uncollected source cannot track entities", async () => {
    await renderPage({ entities: [], source: ipsSource({ enabled: false }) });
    expect(screen.getByText(/This source is not collected \(its profile is disabled/)).toBeInTheDocument();
  });

  it("names the empty dimension when a filter is on", async () => {
    await renderPage({ entities: [], search: { dimension: "DESTINATION_IP" } });
    expect(screen.getByText("No entity of this dimension is tracked for this source.")).toBeInTheDocument();
  });
});

describe("IPS source: failures", () => {
  async function failing(status: number, retryAfter: number | null = null) {
    vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue({ items: [ipsSource()], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "ipsBehaviorEntities").mockRejectedValue(
      status === 0 ? new Error("ECONNREFUSED 10.0.0.9") : new ApiError(status, "x", undefined, undefined, retryAfter),
    );
    vi.spyOn(api, "ipsSourceMetrics").mockResolvedValue({
      items: [],
      total: 0,
      limit: 5000,
      offset: 0,
      range_start: "2026-10-12T08:00:00Z",
      range_end: "2026-10-13T08:00:00Z",
    });
    return render(
      await IpsSourcePage({ params: Promise.resolve({ id: ID }), searchParams: Promise.resolve({}) }),
    );
  }

  it("names the missing permission on 403", async () => {
    await failing(403);
    expect(screen.getByRole("alert")).toHaveTextContent(/read:anomalies/);
  });

  it("asks to sign in again on 401", async () => {
    await failing(401);
    expect(screen.getByRole("alert")).toHaveTextContent(/Your session has expired/);
  });

  it("gives an unknown id a not-found text with a way back to the list", async () => {
    await failing(404);
    expect(screen.getByRole("alert")).toHaveTextContent("This IPS source does not exist.");
    expect(screen.getByRole("link", { name: "Open IPS behavior" })).toHaveAttribute("href", "/behavior/ips");
  });

  it("gives a source the list does not hold the same not-found text", async () => {
    await renderPage({ source: null });
    expect(screen.getByRole("alert")).toHaveTextContent("This IPS source does not exist.");
  });

  it("reports a rate limit with its own wording", async () => {
    await failing(429, 30);
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toMatch(/30/);
    expect(text).not.toMatch(/unreachable/);
  });

  it("says what failed and what to do on an outage, without the exception text", async () => {
    await failing(0);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This IPS source could not be loaded.");
    expect(alert).toHaveTextContent(/backend may be unreachable/);
    expect(alert).not.toHaveTextContent("ECONNREFUSED");
  });

  it("says the chart is absent when only the metrics failed, and keeps the entities", async () => {
    vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue({ items: [ipsSource()], total: 1, limit: 200, offset: 0 });
    vi.spyOn(api, "ipsBehaviorEntities").mockResolvedValue({ items: [ipsEntity()], total: 1, limit: 50, offset: 0 });
    vi.spyOn(api, "ipsSourceMetrics").mockRejectedValue(new Error("down"));
    render(await IpsSourcePage({ params: Promise.resolve({ id: ID }), searchParams: Promise.resolve({}) }));
    expect(screen.getAllByText(/could not be loaded. The rest of the page is unaffected/).length).toBeGreaterThan(0);
    expect(screen.getByText("Destination port 445/tcp")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Events by action/ })).toBeNull();
  });
});

describe("IPS source: boundaries", () => {
  it("announces loading politely", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading this IPS source");
  });

  it("offers a retry and shows no exception text", async () => {
    const reset = vi.fn();
    render(<IpsSourceError error={Object.assign(new Error("secret stack"), { digest: "abc" })} reset={reset} />);
    expect(screen.getByRole("alert").textContent).not.toContain("secret stack");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
  });
});
