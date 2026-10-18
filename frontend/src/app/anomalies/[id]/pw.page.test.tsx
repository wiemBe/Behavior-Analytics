import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomalyDetailPage from "./page";
import {
  bucket, HOSTILE, incident, iso, newValueDetection, point, pwDetail, series, spikeDetection, WATCH_ID,
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
  return pwDetail({
    anomaly_start: new Date(Date.now() - 3600_000).toISOString(),
    detected_at: new Date(Date.now() - 3000_000).toISOString(),
    ...over,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  lastChart.props = null;
  vi.spyOn(api, "propertyWatchValues");
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
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({ observed_measurement: "LOWER_BOUND", detection: spikeDetection({ observed_measurement: "LOWER_BOUND" }) }),
    );
    await renderPage();
    expect(screen.getByText("at least 420")).toBeInTheDocument();
    expect(screen.getAllByText(/events per 5 min/).length).toBeGreaterThan(0);
    expect(screen.getByText(/lower bound/)).toBeInTheDocument();
  });

  it("never looks the value up by its text: the id is the one the detail carries", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    await renderPage();
    expect(api.propertyWatchValues).not.toHaveBeenCalled();
  });

  it("draws the recorded expected range as one band over the incident, and states it as text", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    await renderPage();
    const props = lastChart.props as PropertyWatchSeriesChartProps;
    expect(props.detectionBand).toMatchObject({ low: 80, high: 140 });
    expect(screen.getByText("Expected range for this hour of the week")).toBeInTheDocument();
    expect(screen.getByText(/80 – 140/)).toBeInTheDocument();
  });

  it("says a figure the detector did not record is not recorded, never 0", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({
        detection: spikeDetection({
          expected_low: null, expected_high: null, expected_median: null, baseline_sample_count: null,
          robust_z: null, observed_events: null, ratio: null, threshold: null,
        }),
        observed_value: null, expected_value: null, deviation_ratio: null,
      }),
    );
    await renderPage();
    expect(screen.getAllByText("not recorded").length).toBeGreaterThanOrEqual(5);
    const props = lastChart.props as PropertyWatchSeriesChartProps;
    expect(props.detectionBand).toBeNull();
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it("states the baseline sample count in buckets and the threshold as a z-score", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    await renderPage();
    expect(screen.getByText("36 buckets")).toBeInTheDocument();
    expect(screen.getByText(/threshold 3\.5 \(z-score\)/)).toBeInTheDocument();
  });

  it("labels a new value's figures with what each counts, lower bound as 'at least'", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({
        anomaly_type: "PW_NEW_VALUE",
        observed_measurement: "LOWER_BOUND",
        observed_value: 31,
        expected_value: 0,
        detection: newValueDetection(),
      }),
    );
    await renderPage();
    const figure = (label: string) =>
      within(screen.getByLabelText("Detector figures")).getByText(label).closest("div") as HTMLElement;
    expect(figure("Observed")).toHaveTextContent("at least 31 events");
    expect(figure("Events since first seen")).toHaveTextContent("at least 31 events");
    expect(figure("Buckets with traffic")).toHaveTextContent("at least 3 buckets");
    expect(figure("Minimum to report")).toHaveTextContent("20 events");
    expect(figure("History verified")).toHaveTextContent("2,016 buckets");
    // A new value has no baseline: no band, no expected range.
    expect((lastChart.props as PropertyWatchSeriesChartProps).detectionBand).toBeNull();
    expect(screen.queryByText("Expected range for this hour of the week")).toBeNull();
  });

  it("shows a missing new-value figure as not recorded, not as 0", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({
        anomaly_type: "PW_NEW_VALUE",
        detection: newValueDetection({ buckets_with_traffic: null, history_buckets_verified: null }),
      }),
    );
    await renderPage();
    const figure = (label: string) =>
      within(screen.getByLabelText("Detector figures")).getByText(label).closest("div") as HTMLElement;
    expect(figure("Buckets with traffic")).toHaveTextContent("not recorded");
    expect(figure("History verified")).toHaveTextContent("not recorded");
  });

  it("says there is no detector working when the detail carries none, and does not invent a band", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail({ detection: null }));
    await renderPage();
    expect(screen.getByText(/No detector working was recorded/)).toBeInTheDocument();
    expect((lastChart.props as PropertyWatchSeriesChartProps).detectionBand).toBeNull();
  });

  it("draws no empty evidence panel for a null explanation package, and the facts of a present one", async () => {
    vi.spyOn(api, "anomaly").mockResolvedValue(detail());
    const first = await renderPage();
    expect(screen.queryByText("Evidence collected")).toBeNull();
    first.unmount();
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({
        explanation_package: {
          status: "READY", error: null,
          anomaly_window_start: iso(7), anomaly_window_end: iso(9),
          baseline_window_start: iso(0), baseline_window_end: iso(6),
          comparison_strategy: "x", anomaly_total_events: 1234, baseline_total_events: 456,
          requested_at: null, completed_at: null, collection_duration_ms: null,
          query_provenance: {}, schema_version: 1, dimensions: [],
        } as unknown as PwAnomalyDetail["explanation_package"],
      }),
    );
    await renderPage();
    expect(screen.getByText("Evidence collected")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument();
  });

  it("lists the transitions oldest first with their time and reason", async () => {
    const [first, second] = detail().transitions;
    vi.spyOn(api, "anomaly").mockResolvedValue(detail({ transitions: [second, first] }));
    const { container } = await renderPage();
    const items = [...container.querySelectorAll("li.lifecycle-item")];
    expect(items[0]).toHaveTextContent("above band");
    expect(items[1]).toHaveTextContent("3 consecutive buckets");
    expect(items[0].querySelector("time")).toHaveAttribute("datetime", first.occurred_at);
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

  it("says the value is no longer stored, links to the watch without a selection, and reads no series", async () => {
    const base = detail();
    vi.spyOn(api, "anomaly").mockResolvedValue(
      detail({ pw_value: { ...base.pw_value, value_id: null }, detection: newValueDetection(), anomaly_type: "PW_NEW_VALUE" }),
    );
    await renderPage();
    expect(screen.getByText(/no longer stored \(retention\); the incident is kept/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open this value on the watch" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open the watch" })).toHaveAttribute("href", `/behavior/watches/${WATCH_ID}`);
    expect(api.propertyWatchSeries).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: "pw chart" })).toBeNull();
    // The recorded figures are still there.
    expect(screen.getByText("Events since first seen")).toBeInTheDocument();
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
      detail({
        pw_value: { ...incident().pw_value!, value_key: HOSTILE, components: [HOSTILE] },
        detection: spikeDetection({ claim: HOSTILE, reason: HOSTILE }),
      }),
    );
    const { container } = await renderPage();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getAllByText(new RegExp(HOSTILE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))).length).toBeGreaterThan(1);
    expect(container.querySelector("h1")).toHaveTextContent(HOSTILE);
    expect(screen.getByText("above band")).toBeInTheDocument();
  });

  it("keeps the not-found state for an anomaly the backend does not serve", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(404, "anomaly not found"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("does not exist");
  });
});
