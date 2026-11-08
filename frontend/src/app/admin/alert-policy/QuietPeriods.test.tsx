import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { maintenance, row, session } from "@/components/alerts/suppressionFixtures";
import { ApiError, suppressionApi, type Suppression } from "@/lib/api";
import type { ReadFailure } from "@/lib/suppression";

import { QuietPeriods, subjectHref } from "./QuietPeriods";

const CAN = session(["read:alerts", "admin:outage", "alert:suppress"]);
const READER = session(["read:alerts", "admin:outage"]);

const ANALYST = row();
const PLANNED = row({
  id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  kind: "KNOWN_CHANGE",
  reference: "CHG-2026-17",
  detector_type: null,
  subject_name: "Public portal WAF",
  family: "waf",
  subject_id: "44444444-4444-4444-4444-444444444444",
  owner: "j.okafor",
  reason: "ticketed change",
  hit_count: 0,
});
const WATCH = row({
  id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  family: "property_watch",
  subject_id: "55555555-5555-5555-5555-555555555555",
  detector_type: "PW_NEW_VALUE",
  subject_name: "Admin console logins",
});
const MAINT = maintenance({ subject_name: "Core switch" });

function mount(
  initial: Suppression[] | null,
  failure: ReadFailure | null = null,
  value: SessionValue = CAN,
) {
  render(
    <StaticSession value={value}>
      <QuietPeriods initial={initial} failure={failure} />
    </StaticSession>,
  );
}
const table = () => screen.getByRole("table", { name: /Active quiet periods/ });
const bodyRows = () => within(table()).getAllByRole("row").slice(1);

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the list", () => {
  it("shows every kind: subject link, family, detector, kind, owner, reason, UTC end and count", () => {
    mount([ANALYST, PLANNED, MAINT]);
    const rows = bodyRows();
    expect(rows).toHaveLength(3);
    const first = within(rows[0]);
    expect(first.getByRole("link", { name: "edge-01" })).toHaveAttribute(
      "href",
      "/log-sources/11111111-1111-1111-1111-111111111111",
    );
    expect(first.getByRole("rowheader")).toHaveTextContent("Log source");
    expect(rows[0]).toHaveTextContent("Volume spike");
    expect(rows[0]).toHaveTextContent("Analyst");
    expect(rows[0]).toHaveTextContent("k.osei");
    expect(rows[0]).toHaveTextContent("change window for the firewall");
    expect(rows[0]).toHaveTextContent("11-13 07:00 UTC");
    expect(first.getByRole("cell", { name: "12" })).toHaveClass("num");
    expect(rows[1]).toHaveTextContent("Every detector");
    expect(rows[1]).toHaveTextContent("Planned change");
    expect(rows[1]).toHaveTextContent("CHG-2026-17");
  });

  it("shows a measured zero as 0 and a maintenance row's missing count as a dash", () => {
    mount([PLANNED, MAINT]);
    const [planned, maint] = bodyRows();
    expect(within(planned).getByRole("cell", { name: "0" })).toBeInTheDocument();
    const counts = within(maint).getAllByRole("cell").filter((c) => c.classList.contains("num"));
    expect(counts[0]).toHaveTextContent("—");
    expect(counts[0]).not.toHaveTextContent("0");
  });

  it("links a maintenance row to its log source, reads it as maintenance and gives it no End", () => {
    mount([MAINT]);
    const [r] = bodyRows();
    expect(within(r).getByRole("link", { name: "Core switch" })).toHaveAttribute(
      "href",
      "/log-sources/11111111-1111-1111-1111-111111111111",
    );
    expect(r).toHaveTextContent("Maintenance");
    expect(r).toHaveTextContent("No end set");
    expect(r).toHaveTextContent("Set on the log source");
    expect(within(r).queryByRole("button")).toBeNull();
  });

  it("links a property watch to its own page", () => {
    expect(subjectHref(WATCH)).toBe("/behavior/watches/55555555-5555-5555-5555-555555555555");
    mount([WATCH]);
    expect(screen.getByRole("link", { name: "Admin console logins" })).toHaveAttribute(
      "href",
      "/behavior/watches/55555555-5555-5555-5555-555555555555",
    );
  });

  it("does not link a subject that has left the inventory", () => {
    mount([row({ subject_name: null })]);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("Removed source")).toBeInTheDocument();
  });

  it("keeps the numbers right-aligned and the heading naming the zone", () => {
    mount([ANALYST]);
    expect(within(table()).getByRole("columnheader", { name: "Ends (UTC)" })).toBeInTheDocument();
    expect(within(table()).queryByRole("columnheader", { name: "Family" })).toBeNull();
    expect(within(table()).getByRole("columnheader", { name: "Incidents recorded" })).toHaveClass("num");
  });
});

describe("empty, failed and forbidden", () => {
  it("says nothing is quiet", () => {
    mount([]);
    expect(screen.getByText("Nothing is quiet. Every condition alerts by its policy.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("never says nothing is quiet when it could not read the list", () => {
    mount(null, { kind: "error", text: "The server could not complete the request." });
    expect(screen.getByRole("alert")).toHaveTextContent("Quiet periods could not be read.");
    expect(document.body.textContent).not.toContain("Nothing is quiet");
  });

  it("retries a failed read", async () => {
    const list = vi.spyOn(suppressionApi, "list").mockResolvedValue([ANALYST]);
    mount(null, { kind: "error", text: "x" });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(list).toHaveBeenCalledWith(true));
    expect(await screen.findByRole("table", { name: /Active quiet periods/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("names the permission when the list is forbidden", () => {
    mount(null, { kind: "forbidden", permission: "read:alerts" });
    expect(screen.getByText("Quiet periods are hidden from this account.")).toBeInTheDocument();
    expect(document.body.textContent).toContain("needs the read:alerts permission");
  });
});

describe("ending a quiet period", () => {
  it("asks first, ends by id, reloads the list and says what ended", async () => {
    const end = vi.spyOn(suppressionApi, "end").mockResolvedValue(row({ ended_at: "2026-11-07T08:00:00Z" }));
    const list = vi.spyOn(suppressionApi, "list").mockResolvedValue([]);
    mount([ANALYST, MAINT]);
    await userEvent.click(screen.getByRole("button", { name: /^End the quiet period for edge-01, Volume spike/ }));
    expect(end).not.toHaveBeenCalled();
    expect(screen.getByRole("group", { name: "End this quiet period now?" })).toHaveTextContent(
      "Incidents recorded while quiet stay recorded",
    );
    await userEvent.click(screen.getByRole("button", { name: "End quiet period" }));
    await waitFor(() => expect(end).toHaveBeenCalledWith(ANALYST.id));
    await waitFor(() => expect(list).toHaveBeenCalledWith(true));
    expect(await screen.findByText("Ended the quiet period for edge-01, Volume spike.")).toBeInTheDocument();
    expect(screen.getByText("Nothing is quiet. Every condition alerts by its policy.")).toBeInTheDocument();
  });

  it("keeps the row when the analyst backs out", async () => {
    const end = vi.spyOn(suppressionApi, "end");
    mount([ANALYST]);
    await userEvent.click(screen.getByRole("button", { name: /^End the quiet period/ }));
    await userEvent.click(screen.getByRole("button", { name: "Keep it quiet" }));
    expect(end).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "End this quiet period now?" })).toBeNull();
  });

  it("says a vanished period is gone and reloads", async () => {
    vi.spyOn(suppressionApi, "end").mockRejectedValue(new ApiError(404, "gone"));
    const list = vi.spyOn(suppressionApi, "list").mockResolvedValue([]);
    mount([ANALYST]);
    await userEvent.click(screen.getByRole("button", { name: /^End the quiet period/ }));
    await userEvent.click(screen.getByRole("button", { name: "End quiet period" }));
    expect(await screen.findByText(/no longer exists/)).toBeInTheDocument();
    expect(list).toHaveBeenCalled();
  });

  it("names the permission when ending is refused and keeps the row", async () => {
    vi.spyOn(suppressionApi, "end").mockRejectedValue(
      new ApiError(403, "missing required permission: alert:suppress"),
    );
    mount([ANALYST]);
    await userEvent.click(screen.getByRole("button", { name: /^End the quiet period/ }));
    await userEvent.click(screen.getByRole("button", { name: "End quiet period" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("alert:suppress");
    expect(bodyRows().length).toBeGreaterThan(0);
  });

  it("offers no End to a caller without alert:suppress and names the permission", () => {
    mount([ANALYST, MAINT], null, READER);
    expect(screen.queryByRole("button", { name: /End the quiet period/ })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Action" })).toBeNull();
    expect(screen.getByRole("note")).toHaveTextContent("alert:suppress");
  });
});
