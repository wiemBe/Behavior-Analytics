// UIQ-3: the "Triage: status, owner" fold line (shown up to 1280 px) renders the owner through
// the same OwnerText as the Owner cell, so a long subject has break points.

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "@/app/anomalies/page";
import { OWNER_UNBROKEN_MAX } from "@/components/behavior/AnomalyRows";
import { api, type AnomalySummary } from "@/lib/api";

const OWNER = "analyst.lastname@example.org";

function row(owner: string | null): AnomalySummary {
  return {
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
    detected_at: "2026-11-05T16:55:00Z",
    opened_at: "2026-11-05T16:55:00Z",
    anomaly_start: "2026-11-05T16:55:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    triage: { status: "IN_PROGRESS", owner, disposition: null, updated_at: null },
  } as AnomalySummary;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(Date, "now").mockReturnValue(Date.parse("2026-11-05T19:05:00Z"));
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
});

async function renderList(owner: string | null) {
  vi.spyOn(api, "anomalies").mockResolvedValue({ items: [row(owner)], total: 1, limit: 25, offset: 0 });
  return render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
}
const foldLine = (c: HTMLElement) => c.querySelector("td .cell-fold.fold-md:not(.fold-ev)") as HTMLElement;

describe("the Triage fold line", () => {
  it("keeps its text and breaks the owner after @ and .", async () => {
    const { container } = await renderList(OWNER);
    const line = foldLine(container);
    expect(line.textContent).toBe(`Triage: In progress, ${OWNER}`);
    expect(line.innerHTML).toContain("analyst.<wbr>lastname@<wbr>example.<wbr>org");
    expect(line.querySelectorAll("wbr")).toHaveLength(3);
  });

  it("keeps the unassigned wording without a break", async () => {
    const { container } = await renderList(null);
    const line = foldLine(container);
    expect(line.textContent).toBe("Triage: In progress, Unassigned");
    expect(line.querySelectorAll("wbr")).toHaveLength(0);
    expect(screen.getAllByText("Unassigned").length).toBeGreaterThan(0);
  });
});

describe("the fold line's short owners (r1)", () => {
  it("keeps a short owner whole, in the fold line and in the Owner cell", async () => {
    const { container } = await renderList("m.chen");
    const line = foldLine(container);
    expect(line.textContent).toBe("Triage: In progress, m.chen");
    expect(line.querySelectorAll("wbr")).toHaveLength(0);
    const cell = container.querySelector("td.cell-owner") as HTMLElement;
    expect(cell.textContent).toBe("m.chen");
    expect(cell.querySelectorAll("wbr")).toHaveLength(0);
  });

  it("offers breaks only above the limit", async () => {
    const over = "ab.cdefg@h.ij";
    expect(over.length).toBe(OWNER_UNBROKEN_MAX + 1);
    const limit = "ab.cdefg@h.i"; // 12
    expect(limit.length).toBe(OWNER_UNBROKEN_MAX);
    const first = await renderList(limit);
    expect(foldLine(first.container).querySelectorAll("wbr")).toHaveLength(0);
    expect(foldLine(first.container).textContent).toBe(`Triage: In progress, ${limit}`);
    first.unmount();
    const second = await renderList(over);
    expect(foldLine(second.container).querySelectorAll("wbr").length).toBeGreaterThan(0);
    expect(foldLine(second.container).textContent).toBe(`Triage: In progress, ${over}`);
  });
});
