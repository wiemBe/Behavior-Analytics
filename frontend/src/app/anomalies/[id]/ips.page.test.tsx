import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { ipsCell, ipsDetail, ipsEntityBucket, ipsSourceBucket, ipsSourceDetail, IPS_ENTITY_ID, IPS_SOURCE_ID } from "@/components/ips/fixtures";
import { wafBucket, wafDetail } from "@/components/waf/fixtures";
import { ApiError, api, type AnomalyDetail } from "@/lib/api";

vi.mock("@/components/ips/IpsSeriesChart", () => ({
  IpsSeriesChart: ({ points }: { points: unknown[] }) => (
    <div role="img" aria-label="ips chart" data-points={points.length} />
  ),
}));
vi.mock("@/components/waf/WafRequestChart", () => ({
  WafRequestChart: () => <div role="img" aria-label="waf chart" />,
}));
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: () => <div role="img" aria-label="volume chart" />,
}));

const paged = <T,>(items: T[]) => ({
  items,
  total: items.length,
  limit: 2000,
  offset: 0,
  range_start: "2026-10-12T03:00:00Z",
  range_end: "2026-10-12T15:00:00Z",
});
const cells = (items = [ipsCell()]) => ({ items, total: items.length, limit: 200, offset: 0 });

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "caller").mockResolvedValue({
    permissions: ["read:anomalies"],
    effective_permissions: ["read:anomalies"],
  } as unknown as Awaited<ReturnType<typeof api.caller>>);
});

async function renderPage(id = "i-1") {
  return render(await AnomalyDetailPage({ params: Promise.resolve({ id }) }));
}

describe("dispatch", () => {
  it("opens the IPS investigation for an entity incident, reading only the entity's series", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail());
    const em = vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(paged([ipsEntityBucket()]));
    const eb = vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue(cells());
    const sm = vi.spyOn(api, "sourceMetrics");
    const wm = vi.spyOn(api, "wafEndpointMetrics");
    const { container } = await renderPage();

    expect(container.querySelector("h1")).toHaveTextContent("Destination port 445/tcp");
    expect(screen.getByRole("img", { name: "ips chart" })).toBeInTheDocument();
    expect(em.mock.calls[0][0]).toBe(IPS_ENTITY_ID);
    expect(eb.mock.calls[0][1]).toMatchObject({ metric: "entity_events" });
    expect(sm).not.toHaveBeenCalled();
    expect(wm).not.toHaveBeenCalled();
    expect(container.textContent).not.toMatch(/EPS/);
  });

  it("asks for a window anchored to the incident, six hours either side", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail());
    const em = vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(paged([ipsEntityBucket()]));
    vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue(cells());
    await renderPage();
    expect(em.mock.calls[0][1]).toMatchObject({
      since: "2026-10-12T03:00:00.000Z",
      until: "2026-10-12T15:30:00.000Z",
    });
  });

  it("opens the IPS investigation for a source incident, reading the source's series and its metric", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsSourceDetail());
    const sm = vi.spyOn(api, "ipsSourceMetrics").mockResolvedValue(paged([ipsSourceBucket()]));
    const sb = vi.spyOn(api, "ipsSourceBaselines").mockResolvedValue(cells([]));
    const em = vi.spyOn(api, "ipsEntityMetrics");
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("Source-level");
    expect(sm.mock.calls[0][0]).toBe(IPS_SOURCE_ID);
    expect(sb.mock.calls[0][1]).toMatchObject({ metric: "top1_source_share" });
    expect(em).not.toHaveBeenCalled();
  });

  it("sends an IPS incident whose entity is missing to the IPS view, not the EPS page", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail({ ips_entity: null }));
    const sm = vi.spyOn(api, "sourceMetrics");
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("Entity not recorded");
    expect(sm).not.toHaveBeenCalled();
  });

  it("keeps Phase A on the Phase A view", async () => {
    const phaseA = { ...wafDetail(), subject_kind: "LOG_SOURCE", waf_endpoint: null, anomaly_type: "VOLUME_SPIKE", log_source_name: "LAB Firewall", detection: null } as unknown as AnomalyDetail;
    vi.spyOn(api, "anomaly").mockResolvedValue(phaseA);
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
    const im = vi.spyOn(api, "ipsEntityMetrics");
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("LAB Firewall");
    expect(im).not.toHaveBeenCalled();
  });

  it("keeps WAF on the WAF view", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(wafDetail());
    vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue({ ...paged([wafBucket()]) });
    vi.spyOn(api, "wafEndpointBaselines").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
    const im = vi.spyOn(api, "ipsEntityMetrics");
    const { container } = await renderPage("w-1");
    expect(container.querySelector("h1")).toHaveTextContent("GET /portal/login");
    expect(im).not.toHaveBeenCalled();
  });
});

describe("failures", () => {
  it("keeps the page and says the series could not be loaded when the read fails", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail());
    vi.spyOn(api, "ipsEntityMetrics").mockRejectedValue(new ApiError(500, "boom"));
    vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue(cells());
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("Destination port 445/tcp");
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.queryByRole("img", { name: "ips chart" })).toBeNull();
  });

  it("still draws the series when only the baseline cells fail", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail());
    vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(paged([ipsEntityBucket()]));
    vi.spyOn(api, "ipsEntityBaselines").mockRejectedValue(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.getByRole("img", { name: "ips chart" })).toBeInTheDocument();
    expect(screen.getByText(/BASELINE UNAVAILABLE/)).toBeInTheDocument();
  });

  it("withholds the source link when the caller's grants cannot be read", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail());
    vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(paged([ipsEntityBucket()]));
    vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue(cells());
    vi.spyOn(api, "caller").mockRejectedValue(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.queryByRole("link", { name: "lab-ips-01" })).toBeNull();
  });

  it("links the source for a caller holding admin:ips", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(ipsDetail());
    vi.spyOn(api, "ipsEntityMetrics").mockResolvedValue(paged([ipsEntityBucket()]));
    vi.spyOn(api, "ipsEntityBaselines").mockResolvedValue(cells());
    vi.spyOn(api, "caller").mockResolvedValue({
      permissions: [],
      effective_permissions: ["admin:ips"],
    } as unknown as Awaited<ReturnType<typeof api.caller>>);
    await renderPage();
    expect(screen.getByRole("link", { name: "lab-ips-01" })).toBeInTheDocument();
  });
});
