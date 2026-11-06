// SOC-1b: the anomaly list's triage columns, filters (kept in the URL), Age (QW5) and the
// percent change / "No events for" cells (QW8).

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { chipLabel, withoutFilter } from "@/components/behavior/AnomalyFilters";
import { api, type AnomalySummary } from "@/lib/api";

const NOW = Date.parse("2026-11-05T19:05:00Z");

function row(over: Partial<AnomalySummary> = {}): AnomalySummary {
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
    triage: { status: "NEW", owner: null, disposition: null, updated_at: null },
    ...over,
  };
}

let listed: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(Date, "now").mockReturnValue(NOW);
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
});

async function renderList(items: AnomalySummary[], params: Record<string, string> = {}, total?: number) {
  listed = vi
    .spyOn(api, "anomalies")
    .mockResolvedValue({ items, total: total ?? items.length, limit: 25, offset: 0 });
  return render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
}

function cellsOf(name: RegExp) {
  return within(screen.getByRole("link", { name }).closest("tr")!).getAllByRole("cell", { hidden: true });
}

describe("the Triage and Owner columns", () => {
  it("shows the status and the owner, and 'Unassigned' rather than a blank", async () => {
    await renderList([
      row({ triage: { status: "IN_PROGRESS", owner: "k.osei", disposition: null, updated_at: "2026-11-05T18:00:00Z" } }),
      row({ id: "a-2", log_source_name: "Edge IPS" }),
    ]);
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(headers).toContain("Triage");
    expect(headers).toContain("Owner");
    const first = cellsOf(/LAB Firewall/);
    expect(first.some((c) => c.textContent === "In progress")).toBe(true);
    expect(first.some((c) => c.textContent === "k.osei")).toBe(true);
    const second = cellsOf(/Edge IPS/);
    expect(second.some((c) => c.textContent === "New")).toBe(true);
    expect(second.some((c) => c.textContent === "Unassigned")).toBe(true);
  });

  it("names a closed row's disposition under its status", async () => {
    await renderList([
      row({ triage: { status: "CLOSED", owner: "m.chen", disposition: "NOISE", updated_at: null } }),
    ]);
    expect(cellsOf(/LAB Firewall/).some((c) => c.textContent === "ClosedNoise")).toBe(true);
  });

  it("says the triage was not reported rather than New when the block is absent", async () => {
    await renderList([row({ triage: undefined })]);
    const cells = cellsOf(/LAB Firewall/);
    expect(cells.some((c) => c.textContent === "not reported")).toBe(true);
    expect(cells.some((c) => c.textContent === "New")).toBe(false);
  });

  it("folds triage and owner under the state, for narrow widths", async () => {
    await renderList([
      row({ triage: { status: "IN_PROGRESS", owner: "k.osei", disposition: null, updated_at: null } }),
    ]);
    const row0 = screen.getByRole("link", { name: /LAB Firewall/ }).closest("tr")!;
    expect(row0.querySelector(".fold-md")?.textContent).toBe("Triage: In progress, k.osei");
  });
});

describe("the triage filters", () => {
  it("send what the URL carries to the API", async () => {
    await renderList([row()], { triage_status: "IN_PROGRESS", owner: "me", disposition: "NOISE" });
    expect(listed.mock.calls[0][0]).toMatchObject({
      triage_status: "IN_PROGRESS",
      owner: "me",
      disposition: "NOISE",
    });
  });

  it("read the form's owner kind: none, me, or a named owner", async () => {
    await renderList([row()], { owner_kind: "none" });
    expect(listed.mock.calls[0][0]).toMatchObject({ owner: "none" });
    await renderList([row()], { owner_kind: "named", owner_name: " k.osei " });
    expect(listed.mock.calls[1][0]).toMatchObject({ owner: "k.osei" });
  });

  it("drop a value outside the allow-list instead of forwarding it", async () => {
    await renderList([row()], { triage_status: "DROP TABLE", disposition: "WHATEVER" });
    const sent = listed.mock.calls[0][0];
    expect(sent.triage_status).toBeUndefined();
    expect(sent.disposition).toBeUndefined();
  });

  it("show as chips that each remove only themselves, and survive paging", async () => {
    await renderList([row()], { triage_status: "CLOSED", owner: "none", disposition: "KNOWN_CHANGE" }, 60);
    const chips = screen.getAllByRole("link", { name: /^Remove filter/ });
    const names = chips.map((c) => c.getAttribute("aria-label"));
    expect(names).toContain("Remove filter Triage: Closed");
    expect(names).toContain("Remove filter Owner: unassigned");
    expect(names).toContain("Remove filter Disposition: Known change");
    const closed = chips.find((c) => /Triage: Closed/.test(c.getAttribute("aria-label")!))!;
    const href = closed.getAttribute("href")!;
    expect(href).toContain("owner=none");
    expect(href).toContain("disposition=KNOWN_CHANGE");
    expect(href).not.toContain("triage_status");
    const next = screen.getByRole("link", { name: /next/i });
    expect(next.getAttribute("href")).toContain("triage_status=CLOSED");
    expect(next.getAttribute("href")).toContain("owner=none");
  });

  it("preselect the controls", async () => {
    await renderList([row()], { triage_status: "NEW", owner_kind: "named", owner_name: "k.osei", disposition: "NOISE" });
    expect(screen.getByLabelText("Triage status")).toHaveValue("NEW");
    expect(screen.getByLabelText("Owner", { selector: "select" })).toHaveValue("named");
    expect(screen.getByLabelText("Owner name")).toHaveValue("k.osei");
    expect(screen.getByLabelText("Disposition")).toHaveValue("NOISE");
  });

  it("label every chip through chipLabel and clear through withoutFilter", () => {
    expect(chipLabel("owner", "me", [])).toBe("Owner: me");
    expect(chipLabel("owner", "k.osei", [])).toBe("Owner: k.osei");
    expect(
      withoutFilter(
        { active_only: false, log_source_id: "", instance_id: "", anomaly_type: "", state: "", severity: "",
          evidence_status: "", since: "", until: "", owner: "me", triage_status: "NEW" },
        "owner",
      ),
    ).toBe("/anomalies?triage_status=NEW");
  });

  it("with no match says so and offers to clear the filters", async () => {
    await renderList([], { triage_status: "CLOSED", owner: "nobody" });
    expect(screen.getByText(/No anomalies match these filters/)).toBeInTheDocument();
    const clear = screen.getByRole("link", { name: "Clear all filters" });
    expect(clear).toHaveAttribute("href", "/anomalies");
  });
});

describe("Age (QW5)", () => {
  it("shows time since the start next to the absolute UTC start", async () => {
    await renderList([row()]);
    expect(screen.getByRole("columnheader", { name: "Age" })).toBeInTheDocument();
    const cells = cellsOf(/LAB Firewall/);
    const age = cells.find((c) => c.className.includes("col-age"))!;
    expect(age.textContent).toBe("2 h 10 min");
    expect(cells.find((c) => c.className.includes("col-started") && !c.className.includes("col-age"))).toHaveTextContent(
      "11-05 16:55 UTC",
    );
  });

  it("carries the age beside the start in both folded forms", async () => {
    await renderList([row()]);
    const tr = screen.getByRole("link", { name: /LAB Firewall/ }).closest("tr")!;
    expect(tr.querySelector(".fold-started")?.textContent).toBe("Started 11-05 16:55 UTC");
    expect(tr.querySelector(".fold-started-age")?.textContent).toBe("2 h 10 min ago");
    expect(tr.querySelector(".fold-age")?.textContent).toBe("2 h 10 min ago");
  });

  it("shows a dash, not zero, when there is no start or it lies in the future", async () => {
    await renderList([
      row({ anomaly_start: null, detected_at: "2026-11-05T16:55:00Z" }),
      row({ id: "a-2", log_source_name: "Edge IPS", anomaly_start: "2026-11-06T00:00:00Z" }),
    ]);
    for (const name of [/LAB Firewall/, /Edge IPS/]) {
      const age = cellsOf(name).find((c) => c.className.includes("col-age"))!;
      expect(age.textContent).toBe("—");
    }
  });
});

describe("the deviation cell (QW8)", () => {
  it("puts the percent change next to the multiplier", async () => {
    await renderList([row({ deviation_ratio: 3.2 })]);
    const cell = cellsOf(/LAB Firewall/).find((c) => c.className.includes("col-dev"))!;
    expect(cell.textContent).toBe("3.20x +220.0 %");
  });

  it("shows a drop as a negative change", async () => {
    await renderList([row({ deviation_ratio: 0.4, anomaly_type: "VOLUME_DROP" })]);
    const cell = cellsOf(/LAB Firewall/).find((c) => c.className.includes("col-dev"))!;
    expect(cell.textContent).toBe("0.40x +-60.0 %".replace("+-", "-"));
  });

  it("shows no percent when the backend sent no ratio", async () => {
    await renderList([row({ deviation_ratio: null })]);
    const cell = cellsOf(/LAB Firewall/).find((c) => c.className.includes("col-dev"))!;
    expect(cell.textContent).toBe("—");
  });

  it("says how long nothing arrived for a NO_EVENTS row instead of a dash", async () => {
    await renderList([
      row({ anomaly_type: "NO_EVENTS", observed_value: 0, deviation_ratio: null, anomaly_start: "2026-11-05T18:51:00Z" }),
    ]);
    const cell = cellsOf(/LAB Firewall/).find((c) => c.className.includes("col-dev"))!;
    expect(cell.textContent).toBe("No events for 14 min");
    const tr = screen.getByRole("link", { name: /LAB Firewall/ }).closest("tr")!;
    expect(tr.querySelector(".fold-dev")?.textContent).toBe("No events for 14 min");
  });

  it("uses the recorded duration of a NO_EVENTS row that ended", async () => {
    await renderList([
      row({ anomaly_type: "NO_EVENTS", deviation_ratio: null, duration_seconds: 5400, state: "RESOLVED" }),
    ]);
    const cell = cellsOf(/LAB Firewall/).find((c) => c.className.includes("col-dev"))!;
    expect(cell.textContent).toBe("No events for 1 h 30 min");
  });

  it("does not invent a duration when none is known", async () => {
    await renderList([
      row({ anomaly_type: "NO_EVENTS", deviation_ratio: null, anomaly_start: null }),
    ]);
    const cell = cellsOf(/LAB Firewall/).find((c) => c.className.includes("col-dev"))!;
    expect(cell.textContent).toBe("No events; how long is not recorded");
  });
});
