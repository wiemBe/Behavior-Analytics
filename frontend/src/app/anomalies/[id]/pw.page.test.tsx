import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import {
  bucket, HOSTILE, incident, iso, paged, point, series, value, WATCH_ID,
} from "@/components/propertywatch/fixtures";
import type { PropertyWatchSeriesChartProps } from "@/components/propertywatch/PropertyWatchSeriesChart";
import { ApiError, api, type PwAnomalyDetail } from "@/lib/api";

const lastChart = vi.hoisted(() => ({ props: null as unknown }));
vi.mock("@/components/propertywatch/PropertyWatchSeriesChart", () => ({
  PropertyWatchSeriesChart: (props: PropertyWatchSeriesChartProps) => {
    lastChart.props = props;
    return <div role="img" aria-label="pw chart" />;
  },
}));
vi.mock("@/components/behavior/VolumeChart", () => ({ VolumeChart: () => <div /> }));

function detail(over: Partial<PwAnomalyDetail> = {}): PwAnomalyDetail {
  return {
    ...incident(),
    anomaly_start: new Date(Date.now() - 3600_000).toISOString(),
    detected_at: new Date(Date.now() - 3000_000).toISOString(),
    baseline_version: null,
    policy_version: 1,
    transitions: [
      { from_state: null, to_state: "CANDIDATE", occurred_at: iso(7), bucket_start: iso(7), reason: "above band", actor: "engine", observed_value: 420, expected_value: 100 },
    ],
    explanation_package: null,
    detection: null,
    duration_seconds: null,
    ...over,
  } as PwAnomalyDetail;
}

beforeEach(() => {
  vi.restoreAllMocks();
  lastChart.props = null;
  vi.spyOn(api, "propertyWatchValues").mockResolvedValue(paged([value("id-sb", "svc-backup")]));
  vi.spyOn(api, "propertyWatchSeries").mockResolvedValue({
    hours: 24,
    series: [series("id-sb", "svc-backup", [point(0)])],
  });
  vi.spyOn(api, "propertyWatchBuckets").mockResolvedValue([bucket(0)]);
});

async function renderPage() {
  return render(await AnomalyDetailPage({ params: Promise.resolve({ id: "x" }) }));
}

describe("a property-watch incident", () => {
  it("opens the property-watch investigation, not the Phase A one", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    const sourceMetrics = vi.spyOn(api, "sourceMetrics");
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("svc-backup");
    expect(screen.getByText("Value spike")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "pw chart" })).toBeInTheDocument();
    expect(sourceMetrics).not.toHaveBeenCalled();
  });

  it("links to the watch page with the value selected, by id and never by text", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    await renderPage();
    const link = screen.getByRole("link", { name: "Open this value on the watch" });
    expect(link).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}?value=id-sb`);
    expect(link.getAttribute("href")).not.toContain("svc-backup");
  });

  it("states the observed and expected figures with their unit, and a lower bound as 'at least'", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail({ observed_measurement: "LOWER_BOUND" }));
    await renderPage();
    expect(screen.getByText("at least 420")).toBeInTheDocument();
    expect(screen.getAllByText(/events per 5 min/).length).toBeGreaterThan(0);
    expect(screen.getByText(/lower bound/)).toBeInTheDocument();
  });

  it("asks for the value's series in a window that covers the incident", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    await renderPage();
    const hours = vi.mocked(api.propertyWatchSeries).mock.calls[0][2]!;
    expect(hours).toBeGreaterThanOrEqual(7);
    expect(hours).toBeLessThanOrEqual(168);
    expect(vi.mocked(api.propertyWatchSeries).mock.calls[0][1]).toEqual(["id-sb"]);
  });

  it("draws no chart for an incident older than the series read, and says why", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({
        anomaly_start: "2026-01-01T00:00:00Z",
        anomaly_end: "2026-01-01T01:00:00Z",
        detected_at: "2026-01-01T00:10:00Z",
        resolved_at: "2026-01-01T01:30:00Z",
      }),
    );
    await renderPage();
    expect(screen.queryByRole("img", { name: "pw chart" })).toBeNull();
    expect(screen.getByText(/older than the 7 days the series read covers/)).toBeInTheDocument();
    expect(api.propertyWatchSeries).not.toHaveBeenCalled();
  });

  it("links to the watch without a selection and says the chart is absent when the value is not found", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    vi.spyOn(api, "propertyWatchValues").mockResolvedValue(paged([value("id-x", "someone-else")]));
    await renderPage();
    expect(screen.getByRole("link", { name: "Open the watch" })).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
    expect(screen.getByText(/could not be found among the watch's tracked values/)).toBeInTheDocument();
  });

  it("says the series could not be loaded and keeps the rest of the page", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    vi.spyOn(api, "propertyWatchSeries").mockRejectedValue(new ApiError(500, "x"));
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("svc-backup");
    expect(screen.getByText(/chart is absent rather than empty/)).toBeInTheDocument();
  });

  it("renders a hostile value as text in the heading, and shows the lifecycle", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({ pw_value: { ...incident().pw_value!, value_key: HOSTILE, components: [HOSTILE] } }),
    );
    const { container } = await renderPage();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("h1")).toHaveTextContent(HOSTILE);
    expect(screen.getByText("above band")).toBeInTheDocument();
  });

  it("keeps the not-found state for an anomaly the backend does not serve", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(404, "anomaly not found"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("does not exist");
  });
});
