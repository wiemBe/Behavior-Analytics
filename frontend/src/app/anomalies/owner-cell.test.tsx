// UIQ-2: the Owner cell of the anomaly list. A subject such as analyst.lastname@example.org is one
// unbroken word; at 1440 px it widened the table past its container and the pinned actions cell
// covered its end. The fix is a wrapping rule on the cell (jsdom does not lay out, so the
// contract is the class on the cell and the rule in the stylesheet).

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { api, type AnomalySummary } from "@/lib/api";

const css = readFileSync(resolve(__dirname, "../globals.css"), "utf8");
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

describe("the Owner cell", () => {
  it("carries the wrapping class, holds the whole subject and names it in a title", async () => {
    await renderList(OWNER);
    const cell = screen.getByRole("table").querySelector("td.cell-owner") as HTMLElement;
    expect(cell).not.toBeNull();
    expect(within(cell).getByText(OWNER)).toHaveAttribute("title", OWNER);
  });

  it("breaks at natural points: text content is the subject, <wbr> after each @ and .", async () => {
    await renderList(OWNER);
    const span = screen.getByTitle(OWNER);
    expect(span.textContent).toBe(OWNER);
    const html = span.innerHTML;
    expect(html).toContain("analyst.<wbr>lastname@<wbr>example.<wbr>org");
    expect(span.querySelectorAll("wbr")).toHaveLength(3);
  });

  it("also offers breaks after - and _, without adding characters", async () => {
    await renderList("svc-backup_01@corp.example.org");
    const span = screen.getByTitle("svc-backup_01@corp.example.org");
    expect(span.textContent).toBe("svc-backup_01@corp.example.org");
    expect(span.querySelectorAll("wbr")).toHaveLength(5);
  });

  it("renders a subject with no separators whole, relying on the anywhere fallback", async () => {
    await renderList("analystwithaverylongsubjectwithoutseparators");
    const span = screen.getByTitle("analystwithaverylongsubjectwithoutseparators");
    expect(span.textContent).toBe("analystwithaverylongsubjectwithoutseparators");
    expect(span.querySelectorAll("wbr")).toHaveLength(0);
    expect(span.closest("td")).toHaveClass("cell-owner");
  });

  it("keeps the unowned state readable", async () => {
    await renderList(null);
    const cell = screen.getByRole("table").querySelector("td.cell-owner") as HTMLElement;
    expect(cell).toHaveTextContent("Unassigned");
  });

  it("lets an unbroken subject wrap, with a floor so it does not break letter by letter", () => {
    const m = /\ntd\.cell-owner\s*\{([^}]*)\}/.exec(css);
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/overflow-wrap:\s*anywhere/);
    expect(m![1]).toMatch(/min-width:\s*\d+ch/);
    expect(m![1]).not.toMatch(/nowrap|ellipsis/);
  });
});
