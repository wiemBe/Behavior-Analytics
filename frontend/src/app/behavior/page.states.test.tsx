import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BehaviorPage from "./page";
import { api, type BehaviorSummary, type SourceBehavior } from "@/lib/api";

function summary(over: Partial<BehaviorSummary> = {}): BehaviorSummary {
  return {
    open_anomalies: 0,
    spikes: 0,
    drops: 0,
    silent_sources: 0,
    candidates: 0,
    recovering: 0,
    insufficient_data_sources: 1,
    monitored_sources: 1,
    evidence_pending: 0,
    evidence_failed: 0,
    sources_with_evaluation_faults: 0,
    buckets_not_judged: 0,
    resolved_last_24h: 0,
    recently_resolved: [],
    highest_deviation: [],
    ...over,
  };
}

function unbaselined(over: Partial<SourceBehavior> = {}): SourceBehavior {
  return {
    log_source_id: "s-1",
    name: "Remote Access VPN",
    criticality: "HIGH",
    observed_eps: null,
    expected_eps: null,
    expected_low: null,
    expected_high: null,
    deviation_ratio: null,
    state: "INSUFFICIENT_DATA",
    baseline_sample_count: 0,
    baseline_completeness: 0,
    last_bucket_at: null,
    last_event_at: null,
    open_anomaly_count: 0,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderOverview(sources: SourceBehavior[]) {
  vi.spyOn(api, "behaviorSummary").mockResolvedValue(summary());
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue(sources);
  vi.spyOn(api, "anomalies").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  return render(await BehaviorPage());
}

describe("the worklist's observed / expected cell", () => {
  it("is one dash when neither value was measured, not '— / —'", async () => {
    await renderOverview([unbaselined()]);
    const row = screen.getByRole("link", { name: "Remote Access VPN" }).closest("tr")!;
    const cell = row.querySelector("td.num")!;
    expect(cell.textContent).toBe("—");
  });

  it("keeps a measured observed value beside a missing expectation", async () => {
    await renderOverview([unbaselined({ observed_eps: 4.2 })]);
    const row = screen.getByRole("link", { name: "Remote Access VPN" }).closest("tr")!;
    expect(within(row).getByText(/4\.20/)).toBeInTheDocument();
    expect(row.querySelector("td.num")!.textContent).toBe("4.20 / —");
  });

  it("marks the table and its time cells for the narrow-width rules", async () => {
    await renderOverview([unbaselined({ observed_eps: 4.2 })]);
    expect(document.querySelector("table.attention-table")).not.toBeNull();
    expect(document.querySelector("td.cell-when")).not.toBeNull();
  });
});

describe("the header", () => {
  it("keeps 'Times are UTC.' together so the zone never sits alone on a line", async () => {
    await renderOverview([unbaselined()]);
    expect(screen.getByText("Times are UTC.")).toHaveClass("when-utc");
  });
});

describe("a failed read", () => {
  it("says what the analyst can do", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new Error("ECONNREFUSED"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    vi.spyOn(api, "anomalies").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    render(await BehaviorPage());
    expect(screen.getByRole("alert")).toHaveTextContent(/reload the page/i);
  });
});
