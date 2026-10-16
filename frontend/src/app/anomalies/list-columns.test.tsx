// UI-5.2: the list's secondary columns fold in two steps so the table fits at 1440, 1280 and
// 1024 px with the detail link in reach. The fold itself is CSS (anomalies.css); these tests pin
// the markup it hangs on, and that each folded copy carries the same text as its column.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { ApiError, api, type AnomalySummary } from "@/lib/api";

const ROW: AnomalySummary = {
  id: "a-1",
  log_source_id: "11111111-1111-1111-1111-111111111111",
  log_source_name: "LAB Firewall",
  anomaly_type: "VOLUME_SPIKE",
  state: "OPEN",
  severity: "HIGH",
  observed_value: 6,
  expected_value: 2,
  deviation_ratio: 3,
  robust_z: 13.5,
  absolute_delta: 1200,
  consecutive_buckets: 2,
  confidence: 0.82,
  detected_at: "2026-07-20T10:05:00Z",
  opened_at: "2026-07-20T10:05:00Z",
  anomaly_start: "2026-07-20T10:00:00Z",
  anomaly_end: null,
  resolved_at: null,
  duration_seconds: null,
  evidence_status: "COMPLETE",
  suppressed: false,
  explanation: null,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderList() {
  vi.spyOn(api, "anomalies").mockResolvedValue({ items: [ROW], total: 1, limit: 25, offset: 0 });
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
  return render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
}

describe("list column folding", () => {
  it("marks the table and the two columns that fold at 1440 and 1280 px", async () => {
    const { container } = await renderList();
    expect(container.querySelector("table")?.className).toContain("anomaly-list");
    expect(screen.getByRole("columnheader", { name: "Deviation" }).className).toContain("col-dev");
    expect(screen.getByRole("columnheader", { name: "Started" }).className).toContain(
      "col-started",
    );
  });

  it("gives each folded copy the column's own text", async () => {
    await renderList();
    const row = screen.getByRole("row", { name: /LAB Firewall/ });
    const cells = within(row).getAllByRole("cell", { hidden: true });
    const folded = row.querySelectorAll(".fold-started, .fold-dev");
    expect(folded).toHaveLength(2);
    expect(row.querySelector(".fold-started")?.textContent).toBe("Started 07-20 10:00 UTC");
    expect(row.querySelector(".fold-dev")?.textContent).toBe("Deviation 3.00x");
    expect(cells.some((c) => c.className.includes("col-started"))).toBe(true);
    expect(cells.some((c) => c.className.includes("col-dev"))).toBe(true);
  });
});

describe("list failed read", () => {
  it("says what the analyst can do", async () => {
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(500, "boom"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/reload the page/i);
  });

  it("names the grant on a 403", async () => {
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/read:anomalies/);
  });
});
