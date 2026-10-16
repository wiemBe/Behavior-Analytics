import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourceBehaviorPage from "./page";
import { ApiError, api, type SourceBehavior } from "@/lib/api";

// Failed reads on the source page: what the page must NOT claim when an input
// is missing. The chart is stubbed, as in page.test.tsx.
vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: () => <div data-testid="volume-chart" />,
}));

const ID = "11111111-1111-1111-1111-111111111111";

function behavior(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: ID,
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 6,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 3,
    state: "OPEN",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 1,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderFailedMetrics() {
  vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
  vi.spyOn(api, "logSource").mockRejectedValue(new Error("down"));
  vi.spyOn(api, "sourceMetrics").mockRejectedValue(new Error("timeout"));
  vi.spyOn(api, "anomalies").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.spyOn(api, "sourceBaselines").mockResolvedValue([]);
  return render(
    await SourceBehaviorPage({
      params: Promise.resolve({ id: ID }),
      searchParams: Promise.resolve({}),
    }),
  );
}

describe("a failed metric read", () => {
  it("does not judge collection health from the buckets it never received", async () => {
    await renderFailedMetrics();
    const panel = screen.getByRole("heading", { name: "Collection health" }).closest("section")!;
    expect(within(panel).getByText("Unknown")).toBeInTheDocument();
    expect(panel).toHaveTextContent(/failed request/i);
    expect(panel).not.toHaveTextContent(/Delayed|Partial|Current|No recent collection/);
  });

  it("shows no interval count or lag, which would be a zero for a missing measurement", async () => {
    await renderFailedMetrics();
    const panel = screen.getByRole("heading", { name: "Collection health" }).closest("section")!;
    expect(panel).not.toHaveTextContent(/of \d+ fully observed/);
    expect(panel).not.toHaveTextContent(/ago/);
  });

  it("adds no collection claim to the notes under the metrics", async () => {
    await renderFailedMetrics();
    expect(screen.queryByText(/Collection (delayed|partial|none)/i)).toBeNull();
  });

  it("labels the unread tables as not loaded rather than empty", async () => {
    await renderFailedMetrics();
    expect(screen.getAllByText("not loaded").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("0 most recent")).toBeNull();
  });
});

describe("a window whose edges both hold a bucket", () => {
  it("never reports more observed intervals than the window holds", async () => {
    // 6 h of 5 min buckets is 72 intervals; both edges stored makes 73.
    const buckets = Array.from({ length: 73 }, (_, i) => ({
      bucket_start: new Date(Date.UTC(2026, 6, 20, 4, 0) + i * 300_000).toISOString(),
      bucket_seconds: 300,
      event_count: 600,
      average_eps: 2,
      peak_eps: null,
      completeness: "COMPLETE" as const,
      last_event_at: null,
    }));
    vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
    vi.spyOn(api, "logSource").mockRejectedValue(new Error("down"));
    vi.spyOn(api, "sourceMetrics").mockResolvedValue(buckets);
    vi.spyOn(api, "anomalies").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    vi.spyOn(api, "sourceBaselines").mockResolvedValue([]);
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByText(/73 of 73 fully observed/)).toBeInTheDocument();
    expect(screen.queryByText(/73 of 72/)).toBeNull();
  });
});

describe("the error state", () => {
  it("keeps the shared page header and a way back for an unknown source", async () => {
    vi.spyOn(api, "sourceBehavior").mockRejectedValue(new ApiError(404, "nope"));
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("heading", { level: 1, name: "Source behavior" })).toBeInTheDocument();
    expect(document.querySelector("header.page-header")).not.toBeNull();
    expect(screen.getByRole("link", { name: /All sources/ })).toHaveAttribute(
      "href",
      "/behavior/sources",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/choose another/i);
  });

  it("tells the analyst what to do when the backend cannot be reached", async () => {
    vi.spyOn(api, "sourceBehavior").mockRejectedValue(new Error("ECONNREFUSED"));
    render(
      await SourceBehaviorPage({
        params: Promise.resolve({ id: ID }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/reload the page/i);
  });
});
