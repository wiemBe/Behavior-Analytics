import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlertDetailPage from "./page";
import { ApiError, api, type Alert, type Notification } from "@/lib/api";

// States this route renders, from `page.tsx`:
//  - either read fails: a "Could not load alert <id>" notice (a 404 is not special);
//  - the normal detail: the title, the severity / status / occurrence / fingerprint
//    line, the Evidence card, the Lifecycle card, the operator actions and the
//    notification delivery history;
//  - no evidence captured, and evidence with some fields missing;
//  - an alert with no notifications: the "No notifications" notice;
//  - the resolved state, where the operator actions are replaced by a closed notice;
//  - honesty: null lifecycle fields and null delivery fields are em dashes.
// The severity, status and notification-status pills carry a tone class the page uses
// as meaning (sevTone; notification SENT -> ok, DEAD_LETTER -> crit, FAILED -> warn),
// so those are asserted. One honesty gap is recorded as `it.todo`: the lifecycle and
// delivery times are locale-formatted with no timezone.

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a-1",
    fingerprint: "vol:227:1789",
    title: "Event volume spike on Core Firewall",
    description: null,
    severity: "HIGH",
    status: "OPEN",
    source_type: "ANOMALY",
    opened_at: "2026-10-15T07:00:00Z",
    first_seen_at: "2026-10-15T07:00:00Z",
    last_seen_at: "2026-10-15T08:30:00Z",
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 4,
    evidence_snapshot: { observed_value: 5.98, expected_value: 1.98, reason: "average EPS 5.98 is above the baseline median" },
    source_anomaly_ids: ["an-1", "an-2"],
    ...over,
  };
}

function notification(over: Partial<Notification> = {}): Notification {
  return {
    id: "n-1",
    alert_id: "a-1",
    channel: "EMAIL",
    target: "soc@example.com",
    transition: "OPENED",
    status: "SENT",
    attempts: 1,
    max_attempts: 3,
    next_attempt_at: null,
    sent_at: "2026-10-15T07:00:05Z",
    error_message: null,
    ...over,
  };
}

async function renderPage(id = "a-1") {
  return render(await AlertDetailPage({ params: Promise.resolve({ id }) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "alert").mockResolvedValue(alert());
  vi.spyOn(api, "alertNotifications").mockResolvedValue([notification()]);
});

describe("alert detail states", () => {
  it("names the alert it could not load and does not leak the failure", async () => {
    vi.spyOn(api, "alert").mockRejectedValue(new ApiError(500, "secret"));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    await renderPage("a-9");
    expect(screen.getByRole("heading", { name: "Alert Detail" })).toBeInTheDocument();
    const notice = screen.getByText(/Could not load alert/);
    expect(notice).toHaveTextContent("a-9");
    expect(notice).not.toHaveTextContent("secret");
  });

  it("says when the alert has no notifications", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No notifications for this alert.")).toBeInTheDocument();
  });
});

describe("alert detail contents", () => {
  it("names the alert and its lifecycle at a glance", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { name: "Event volume spike on Core Firewall", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toHaveClass("crit");
    expect(screen.getByText("OPEN")).toBeInTheDocument();
    expect(screen.getByText(/seen 4×/)).toBeInTheDocument();
    expect(screen.getByText("vol:227:1789").tagName).toBe("CODE");
  });

  it("renders the captured evidence with its reason", async () => {
    await renderPage();
    expect(screen.getByText("average EPS 5.98 is above the baseline median")).toBeInTheDocument();
    expect(screen.getByText("Observed").closest("tr")).toHaveTextContent("5.98");
    expect(screen.getByText("Expected").closest("tr")).toHaveTextContent("1.98");
  });

  it("says when no evidence was captured rather than showing an empty table", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ evidence_snapshot: {} }));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("no evidence captured")).toBeInTheDocument();
  });

  it("draws a missing evidence field as an em dash", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ evidence_snapshot: { observed_value: 5.98 } }));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("Expected").closest("tr")).toHaveTextContent("—");
  });

  it("shows the lifecycle fields and falls back to em dashes", async () => {
    await renderPage();
    const lifecycle = screen.getByText("Lifecycle", { selector: ".k" }).closest(".card") as HTMLElement;
    expect(lifecycle).toHaveTextContent("Acknowledged by: —");
    expect(lifecycle).toHaveTextContent("Resolved by: —");
    expect(lifecycle).toHaveTextContent("Resolution: —");
    expect(lifecycle).toHaveTextContent("Source anomalies: 2");
  });

  it("offers acknowledge and resolve while an alert is open", async () => {
    await renderPage();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resolve" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Resolution reason" })).toBeInTheDocument();
  });

  it("closes the operator actions on a resolved alert", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ status: "RESOLVED", resolved_by: "analyst@example.com" }));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    await renderPage();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
    expect(screen.getByText("This alert is resolved. No further action is available.")).toBeInTheDocument();
    const lifecycle = screen.getByText("Lifecycle", { selector: ".k" }).closest(".card") as HTMLElement;
    expect(lifecycle).toHaveTextContent("Resolved by: analyst@example.com");
  });
});

describe("notification delivery history", () => {
  it("lists every delivery column with tries and an em dash for absent fields", async () => {
    await renderPage();
    // The evidence card holds the first table; the delivery history is the second.
    const table = screen.getAllByRole("table")[1];
    for (const header of [
      "Channel",
      "Transition",
      "Status",
      "Attempts",
      "Sent",
      "Next attempt",
      "Error",
    ]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveTextContent("EMAIL");
    expect(row).toHaveTextContent("OPENED");
    expect(within(row).getByText("SENT")).toHaveClass("ok");
    expect(row).toHaveTextContent("1/3");
    // Next attempt and error were never set.
    expect(within(row).getAllByText("—")).toHaveLength(2);
  });

  it("tones a dead-letter and a failed delivery as problems", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({ id: "n-1", status: "DEAD_LETTER", error_message: "SMTP 550" }),
      notification({ id: "n-2", status: "FAILED", attempts: 3, max_attempts: 3 }),
    ]);
    await renderPage();
    expect(screen.getByText("DEAD_LETTER")).toHaveClass("crit");
    expect(screen.getByText("FAILED")).toHaveClass("warn");
    expect(screen.getByText("SMTP 550")).toBeInTheDocument();
  });
});

describe("alert detail honesty defects", () => {
  // `toLocaleString()` renders the lifecycle and delivery times with no timezone.
  it.todo("render the opened and delivery times with their timezone");
});
