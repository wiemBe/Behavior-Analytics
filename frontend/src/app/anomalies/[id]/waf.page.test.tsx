import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import { endpointRef, wafBucket, wafDetail } from "@/components/waf/fixtures";
import { ApiError, api, type AnomalyDetail, type PagedWafMetrics } from "@/lib/api";

// Both charts are canvas-backed; each has its own test. Stubbed to a slot.
vi.mock("@/components/waf/WafRequestChart", () => ({
  WafRequestChart: ({ buckets }: { buckets: unknown[] }) => (
    <div role="img" aria-label="waf chart" data-buckets={buckets.length} />
  ),
}));
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: () => <div role="img" aria-label="volume chart" />,
}));

function metrics(items = [wafBucket()]): PagedWafMetrics {
  return {
    items,
    total: items.length,
    limit: 2000,
    offset: 0,
    range_start: "2026-10-12T03:00:00Z",
    range_end: "2026-10-12T15:00:00Z",
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(id = "w-1") {
  return render(await AnomalyDetailPage({ params: Promise.resolve({ id }) }));
}

describe("a WAF subject", () => {
  it("opens the WAF investigation, not the Phase A one", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(wafDetail());
    const wafMetrics = vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue(metrics());
    const sourceMetrics = vi.spyOn(api, "sourceMetrics");
    const { container } = await renderPage();

    expect(container.querySelector("h1")).toHaveTextContent("GET /portal/login");
    expect(screen.getByRole("img", { name: "waf chart" })).toBeInTheDocument();
    expect(wafMetrics).toHaveBeenCalledTimes(1);
    expect(sourceMetrics).not.toHaveBeenCalled();
  });

  it("asks for the endpoint's own buckets, in a window anchored to the anomaly", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(wafDetail());
    const wafMetrics = vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue(metrics());
    await renderPage();

    const [endpointId, params] = wafMetrics.mock.calls[0];
    expect(endpointId).toBe(endpointRef().id);
    // Anomaly 09:00 to 09:30, six hours of context either side.
    expect(params).toMatchObject({ since: "2026-10-12T03:00:00.000Z", until: "2026-10-12T15:30:00.000Z" });
    expect(params?.limit).toBeLessThanOrEqual(5000);
  });

  it("keeps the investigation and says the history could not be loaded when the buckets fail", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(wafDetail());
    vi.spyOn(api, "wafEndpointMetrics").mockRejectedValue(new ApiError(500, "boom"));
    const { container } = await renderPage();

    expect(container.querySelector("h1")).toHaveTextContent("GET /portal/login");
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.queryByRole("img", { name: "waf chart" })).not.toBeInTheDocument();
  });

  it("distinguishes an empty history from a failed one", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(wafDetail());
    vi.spyOn(api, "wafEndpointMetrics").mockResolvedValue(metrics([]));
    await renderPage();
    // Empty: the chart slot is drawn (and says so itself); no failure alert.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "waf chart" })).toHaveAttribute("data-buckets", "0");
  });
});

describe("a Phase A subject", () => {
  it("keeps the Phase A investigation and never calls the WAF metrics", async () => {
    const phaseA = { ...wafDetail(), subject_kind: "LOG_SOURCE", waf_endpoint: null, anomaly_type: "VOLUME_SPIKE", log_source_name: "LAB Firewall", detection: null } as unknown as AnomalyDetail;
    vi.spyOn(api, "anomaly").mockResolvedValue(phaseA);
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
    const wafMetrics = vi.spyOn(api, "wafEndpointMetrics");
    const { container } = await renderPage();

    expect(container.querySelector("h1")).toHaveTextContent("LAB Firewall");
    expect(wafMetrics).not.toHaveBeenCalled();
  });

  it("treats a WAF subject with no endpoint as Phase A rather than crashing", async () => {
    const odd = { ...wafDetail(), waf_endpoint: null, log_source_name: "Odd" } as unknown as AnomalyDetail;
    vi.spyOn(api, "anomaly").mockResolvedValue(odd);
    vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("Odd");
  });
});

describe("failures", () => {
  it("says the anomaly does not exist on a 404", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(404, "not found"));
    await renderPage("nope");
    expect(screen.getByRole("alert")).toHaveTextContent("This anomaly does not exist.");
  });
});
