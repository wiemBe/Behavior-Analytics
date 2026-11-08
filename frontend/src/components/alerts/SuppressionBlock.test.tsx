import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, api, suppressionApi } from "@/lib/api";
import type { SuppressTarget } from "@/lib/suppression";

import { SuppressionBlock } from "./SuppressionBlock";
import { forAnomaly, row, session } from "./suppressionFixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

const TARGET: SuppressTarget = { via: "anomaly", family: "log_source", anomalyId: "22222222-2222-2222-2222-222222222222" };
const CAN = session(["read:alerts", "read:anomalies", "alert:suppress"]);
const NONE = forAnomaly({ suppression: null, hit_count: 0 });

function mount(value: SessionValue = CAN) {
  render(
    <StaticSession value={value}>
      <SuppressionBlock target={TARGET} />
    </StaticSession>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "anomaly").mockResolvedValue({ log_source_name: "Linux audit fleet" } as never);
});

describe("loading and nothing active", () => {
  it("says it is checking while the read is out", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.getByRole("status")).toHaveTextContent("Checking whether this condition is quiet.");
  });

  it("asks for the family and the anomaly it was given", async () => {
    const spy = vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(NONE);
    mount();
    await waitFor(() => expect(spy).toHaveBeenCalledWith("log_source", TARGET.anomalyId));
  });

  it("draws no banner when nothing is active, only the action", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(NONE);
    mount();
    expect(await screen.findByRole("button", { name: "Don't alert this again" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Quiet period" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/Quiet until/);
  });
});

describe("an active quiet period", () => {
  const BREAK = {
    id: "33333333-3333-3333-3333-333333333333",
    title: "Volume spike on edge-01",
    severity: "high",
    opened_at: "2026-11-07T10:00:00Z",
    occurrence_count: 3,
  };

  it("states until when, by whom, for how long and what was recorded meanwhile", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly());
    mount();
    const banner = await screen.findByRole("region", { name: "Quiet period" });
    expect(banner).toHaveTextContent(
      "Quiet until 2026-11-13 07:00:00 UTC by k.osei (7 days). 12 incidents recorded while quiet.",
    );
    expect(within(banner).getByText("change window for the firewall")).toBeInTheDocument();
    expect(within(banner).getByText("Analyst")).toBeInTheDocument();
    expect(within(banner).getByText("Volume spike")).toBeInTheDocument();
    expect(banner).toHaveTextContent("Incidents are always recorded");
  });

  it("says one incident and none in their own words, a measured zero included", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly({ hit_count: 1 }));
    mount();
    expect(await screen.findByText(/1 incident recorded while quiet/)).toBeInTheDocument();
  });

  it("names a planned change with its reference and a whole-source scope", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(
      forAnomaly({ suppression: row({ kind: "KNOWN_CHANGE", reference: "CHG-2026-17", detector_type: null }) }),
    );
    mount();
    const banner = await screen.findByRole("region", { name: "Quiet period" });
    expect(banner).toHaveTextContent("Planned change · Reference CHG-2026-17");
    expect(banner).toHaveTextContent("every detector of edge-01");
  });

  it("lists the alerts that broke through, linked, or says none did", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly({ breakthrough_alerts: [BREAK] }));
    mount();
    const link = await screen.findByRole("link", { name: "Volume spike on edge-01" });
    expect(link).toHaveAttribute("href", `/alerts/${BREAK.id}`);
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    expect(document.body.textContent).toContain("seen 3 times");
    expect(document.body.textContent).toContain("2026-11-07 10:00:00 UTC");
  });

  it("says no alert broke through when none did", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly());
    mount();
    expect(await screen.findByText("No alert broke through the quiet period.")).toBeInTheDocument();
  });

  it("changes the action to change the quiet period", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly());
    mount();
    expect(await screen.findByRole("button", { name: "Change the quiet period" })).toBeInTheDocument();
  });
});

describe("End now", () => {
  it("asks first, then ends the period and reads the state again", async () => {
    const read = vi
      .spyOn(suppressionApi, "forAnomaly")
      .mockResolvedValueOnce(forAnomaly())
      .mockResolvedValue(NONE);
    const end = vi.spyOn(suppressionApi, "end").mockResolvedValue(row({ ended_at: "2026-11-07T08:00:00Z" }));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "End now" }));
    expect(end).not.toHaveBeenCalled();
    const ask = screen.getByRole("group", { name: "End this quiet period now?" });
    expect(ask).toHaveTextContent("Incidents recorded while quiet stay recorded");
    await userEvent.click(within(ask).getByRole("button", { name: "End quiet period" }));
    await waitFor(() => expect(end).toHaveBeenCalledWith(row().id));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Quiet period" })).toBeNull());
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("status")).toHaveTextContent("The quiet period was ended.");
  });

  it("keeps it quiet when the analyst backs out", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly());
    const end = vi.spyOn(suppressionApi, "end");
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "End now" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep it quiet" }));
    expect(end).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "End now" })).toHaveFocus();
  });

  it("says when the period is already gone and reads the state again", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValueOnce(forAnomaly()).mockResolvedValue(NONE);
    vi.spyOn(suppressionApi, "end").mockRejectedValue(new ApiError(404, "gone"));
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "End now" }));
    await userEvent.click(screen.getByRole("button", { name: "End quiet period" }));
    expect(await screen.findByText(/no longer exists/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Quiet period" })).toBeNull();
  });

  it("keeps the confirmation open with the reason when ending fails", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly());
    vi.spyOn(suppressionApi, "end").mockRejectedValue(
      new ApiError(403, "missing required permission: alert:suppress"),
    );
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "End now" }));
    await userEvent.click(screen.getByRole("button", { name: "End quiet period" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("alert:suppress");
    expect(screen.getByRole("button", { name: "End quiet period" })).toBeEnabled();
  });
});

describe("its own failure states", () => {
  it("says the state is unknown when the read fails, and retries", async () => {
    const read = vi
      .spyOn(suppressionApi, "forAnomaly")
      .mockRejectedValueOnce(new ApiError(500, "x"))
      .mockResolvedValue(NONE);
    mount();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not be read");
    expect(alert).toHaveTextContent("Whether this condition is quiet is unknown.");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("names the permission the read needs when it is forbidden", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockRejectedValue(
      new ApiError(403, "missing required permission: read:anomalies"),
    );
    mount();
    expect(await screen.findByText(/hidden from this account/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("needs the read:anomalies permission");
  });

  it("names both reads when the server names none", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockRejectedValue(new ApiError(403, "forbidden"));
    mount();
    expect(await screen.findByText(/hidden from this account/)).toBeInTheDocument();
    expect(document.body.textContent).toContain("read:alerts and read:anomalies permissions");
  });

  it("still offers the action to a caller who may quiet it when the banner cannot load", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockRejectedValue(new ApiError(500, "x"));
    mount();
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Don't alert this again" })).toBeInTheDocument();
  });

  it("shows the banner but not the action to a caller without alert:suppress", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(forAnomaly());
    mount(session(["read:alerts", "read:anomalies"]));
    expect(await screen.findByRole("region", { name: "Quiet period" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("alert:suppress");
  });
});

describe("naming what would be quieted", () => {
  const ALERT: SuppressTarget = {
    via: "alert",
    alertId: "a-1",
    family: "log_source",
    anomalyId: TARGET.anomalyId,
    alertDetector: "VOLUME_DROP",
  };
  const open = async () =>
    userEvent.click(await screen.findByRole("button", { name: "Don't alert this again" }));

  it("names the condition's detector and source from the server's read, not the alert's title", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(NONE);
    render(<StaticSession value={CAN}><SuppressionBlock target={{ ...ALERT, alertDetector: "VOLUME_SPIKE" }} /></StaticSession>);
    await open();
    await waitFor(() =>
      expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
        "Alerts for Volume spike on Linux audit fleet are held back",
      ),
    );
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("says so when the alert's detector differs from its source anomaly's", async () => {
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(NONE);
    render(<StaticSession value={CAN}><SuppressionBlock target={ALERT} /></StaticSession>);
    await open();
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("The alert is for Volume drop.");
    expect(note).toHaveTextContent("Volume spike on Linux audit fleet");
  });

  it("falls back to generic wording, with no name, when the source cannot be read", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(500, "x"));
    vi.spyOn(suppressionApi, "forAnomaly").mockResolvedValue(NONE);
    render(<StaticSession value={CAN}><SuppressionBlock target={TARGET} /></StaticSession>);
    await open();
    expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "Alerts for Volume spike on this source are held back",
    );
  });
});
