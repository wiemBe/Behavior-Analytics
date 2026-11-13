// SOC-13d: the source page lists the analyst training exclusions under its baseline quality, and
// shades the effective ones on its volume chart.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SourceBehaviorPage from "./page";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { CAN, READ_ONLY, SOURCE, exclusion } from "@/components/baseline/fixtures";
import { ApiError, api, trainingExclusionApi, type SourceBehavior } from "@/lib/api";

vi.mock("@/components/behavior/VolumeChart", () => ({
  VolumeChart: ({ exclusions }: { exclusions?: Array<{ start: string; end: string }> }) => (
    <div data-testid="chart" data-exclusions={JSON.stringify(exclusions ?? [])} />
  ),
}));

function behavior(): SourceBehavior {
  return {
    log_source_id: SOURCE,
    name: "LAB Firewall",
    criticality: "HIGH",
    observed_eps: 2,
    expected_eps: 2,
    expected_low: 1.7,
    expected_high: 2.3,
    deviation_ratio: 1,
    state: "NORMAL",
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    last_bucket_at: "2026-07-20T10:00:00Z",
    last_event_at: "2026-07-20T10:04:00Z",
    open_anomaly_count: 0,
  } as SourceBehavior;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "sourceBehavior").mockResolvedValue(behavior());
  vi.spyOn(api, "logSource").mockRejectedValue(new Error("down"));
  vi.spyOn(api, "sourceMetrics").mockResolvedValue([]);
  vi.spyOn(api, "anomalies").mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.spyOn(api, "sourceBaselines").mockResolvedValue([]);
});

async function renderPage(value: SessionValue = CAN) {
  return render(
    <StaticSession value={value}>
      {await SourceBehaviorPage({
        params: Promise.resolve({ id: SOURCE }),
        searchParams: Promise.resolve({}),
      })}
    </StaticSession>,
  );
}

describe("Baseline training exclusions on the source page", () => {
  it("lists every effect state under the quality section, newest first", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockResolvedValue({
      items: [
        exclusion({ id: "p" }),
        exclusion({ id: "a", state: "APPLIED" }),
        exclusion({ id: "o", state: "OUTSIDE_LOOKBACK" }),
        exclusion({ id: "r", state: "REVOKED", revoked_by: "a.rivera", revoked_at: "2026-11-05T00:00:00Z", revocation_reason: "wrong span" }),
      ],
      limit: 100,
    });
    await renderPage();
    const section = screen.getByRole("heading", { name: "Data quality" }).closest("section")!;
    const list = within(section).getByRole("group", { name: "Baseline training exclusions" });
    const rows = within(list).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Pending next baseline rebuild"),
      expect.stringContaining("Applied by a baseline rebuild"),
      expect.stringContaining("Outside the baseline lookback"),
      expect.stringContaining("Revoked"),
    ]);
    expect(within(list).getAllByRole("button", { name: "Revoke" })).toHaveLength(3);
  });

  it("says none when none exist", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockResolvedValue({ items: [], limit: 100 });
    await renderPage();
    expect(screen.getByText("No analyst training exclusions affect this baseline.")).toBeInTheDocument();
  });

  it("says it could not load them, and Retry reads them again", async () => {
    const read = vi.spyOn(trainingExclusionApi, "forSource").mockRejectedValueOnce(new ApiError(500, "boom"));
    await renderPage();
    expect(screen.getByText("Training exclusions could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Data quality" })).toBeInTheDocument();
    read.mockResolvedValue({ items: [exclusion()], limit: 100 });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "Analyst training exclusions" })).toBeInTheDocument();
  });

  it("names read:anomalies when the caller may not read them", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockRejectedValue(new ApiError(403, "forbidden"));
    await renderPage();
    expect(screen.getByText(/needs the read:anomalies permission/)).toBeInTheDocument();
  });

  it("shows a read-only caller the rows without Revoke", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockResolvedValue({ items: [exclusion()], limit: 100 });
    await renderPage(READ_ONLY);
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
  });

  it("shades only the effective exclusions on the chart, with the range in text", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockResolvedValue({
      items: [exclusion(), exclusion({ id: "r", state: "REVOKED", starts_at: "2026-11-02T00:00:00Z", ends_at: "2026-11-02T01:00:00Z" })],
      limit: 100,
    });
    await renderPage();
    expect(JSON.parse(screen.getByTestId("chart").getAttribute("data-exclusions")!)).toEqual([
      { start: "2026-11-04T08:00:00Z", end: "2026-11-04T10:00:00Z" },
    ]);
    expect(screen.getByText(/Excluded from baseline training: 04 Nov 08:00–10:00 UTC/)).toBeInTheDocument();
  });
});
