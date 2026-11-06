// SOC-7b: the anomaly list's relative range chips, its reproducible address, and the SOC-1b
// filters (triage status, owner, disposition) that must keep working beside them.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
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

async function renderList(params: Record<string, string> = {}, items = [row()], total = 60) {
  listed = vi.spyOn(api, "anomalies").mockResolvedValue({ items, total, limit: 25, offset: 0 });
  return render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
}

const hrefOf = (name: string) => screen.getByRole("link", { name }).getAttribute("href")!;

describe("the range chips", () => {
  it("sends a relative since for a chip and no until, and keeps the chip relative in links", async () => {
    await renderList({ range: "7d", triage_status: "NEW" });
    const sent = listed.mock.calls[0][0];
    expect(sent.since).toBe("2026-10-29T19:05:00.000Z");
    expect(sent.until).toBeUndefined();
    expect(hrefOf("Last 24 hours")).toBe("/anomalies?triage_status=NEW&range=24h");
    // The active chip links to the view without a range.
    expect(hrefOf("Last 7 days")).toBe("/anomalies?triage_status=NEW");
    expect(screen.getByRole("link", { name: "Last 7 days" })).toHaveAttribute("aria-current", "true");
    // Paging keeps the chip, not the instants it resolved to.
    expect(hrefOf("Next")).toBe("/anomalies?range=7d&triage_status=NEW&offset=25");
  });

  it("prints the window it covers in absolute UTC", async () => {
    await renderList({ range: "24h" });
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent(
      "2026-11-04 19:05 to 2026-11-05 19:05 UTC (now)",
    );
  });

  it("says the default window when no range is set", async () => {
    await renderList();
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent(
      "Default window, last 24 hours: 2026-11-04 19:05 UTC to 2026-11-05 19:05 UTC",
    );
    expect(listed.mock.calls[0][0].since).toBeUndefined();
  });

  it("writes a Custom window as absolute UTC and opens the inputs for it", async () => {
    await renderList({ since: "2026-11-05T00:00", until: "2026-11-05T12:00" });
    expect(listed.mock.calls[0][0].since).toBe("2026-11-05T00:00:00.000Z");
    expect(screen.getByLabelText("Start time (UTC)")).toHaveValue("2026-11-05T00:00");
    expect(hrefOf("Next")).toContain("since=2026-11-05T00%3A00%3A00Z&until=2026-11-05T12%3A00%3A00Z");
    expect(hrefOf("Next")).not.toContain("range=");
  });

  it("drops an unknown range with a note instead of a 422", async () => {
    await renderList({ range: "2d" });
    expect(screen.getByText(/Ignored an unknown range value/)).toBeInTheDocument();
    expect(listed.mock.calls[0][0].since).toBeUndefined();
  });

  it("offers the chip as a removable filter chip", async () => {
    await renderList({ range: "30d" });
    expect(
      screen.getByRole("link", { name: "Remove filter Range: last 30 days" }),
    ).toHaveAttribute("href", "/anomalies");
  });

  it("copies the view's own address", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await renderList({ range: "7d", triage_status: "NEW", severity: "BOGUS" });
    fireEvent.click(screen.getByRole("button", { name: "Copy a link to this view" }));
    await screen.findByText("Link copied");
    expect(writeText).toHaveBeenCalledWith(
      `${window.location.origin}/anomalies?range=7d&triage_status=NEW`,
    );
  });
});

describe("the SOC-1b filters beside the chips", () => {
  it("still sends triage status, owner and disposition from the same URL keys", async () => {
    await renderList({
      range: "7d",
      triage_status: "IN_PROGRESS",
      owner: "k.osei",
      disposition: "NOISE",
    });
    expect(listed.mock.calls[0][0]).toMatchObject({
      triage_status: "IN_PROGRESS",
      owner: "k.osei",
      disposition: "NOISE",
    });
  });

  it("keeps the owner and disposition inside More filters, open when one is active", async () => {
    const { container } = await renderList({ owner: "me" });
    const more = container.querySelector("details.filter-more") as HTMLElement;
    expect(more).toHaveAttribute("open");
    expect(within(more).getByLabelText("Owner")).toHaveValue("me");
    expect(within(more).getByLabelText("Disposition")).toBeInTheDocument();
    expect(screen.getByLabelText("Triage status")).toBeInTheDocument();
  });

  it("keeps a triage filter when a chip is clicked and when the form is submitted", async () => {
    const { container } = await renderList({ triage_status: "NEW", range: "24h" });
    const form = container.querySelector("form#anomaly-filters") as HTMLFormElement;
    // The chip travels with the form while no Custom window is being typed.
    expect(new FormData(form).get("range")).toBe("24h");
    expect(new FormData(form).get("triage_status")).toBe("NEW");
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    const data = new FormData(form);
    expect(data.get("range")).toBeNull();
    expect(data.has("since")).toBe(true);
  });
});
