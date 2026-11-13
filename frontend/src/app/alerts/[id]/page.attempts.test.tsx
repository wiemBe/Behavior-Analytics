import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlertDetailPage from "./page";
import { ApiError, api, type Alert, type Notification } from "@/lib/api";
import { setTestLocale } from "../../../../vitest.setup";

// SOC-12f: the delivery table shows the target label and the last attempt (class, status, UTC time)
// from `attempt_log`, with a disclosure for up to eight attempts. No URL appears anywhere.

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

const ALERT: Alert = {
  id: "a-1",
  fingerprint: "vol:227:1789",
  title: "Event volume spike on Core Firewall",
  description: null,
  severity: "HIGH",
  status: "OPEN",
  source_type: "ANOMALY",
  opened_at: "2026-11-13T07:00:00Z",
  first_seen_at: "2026-11-13T07:00:00Z",
  last_seen_at: "2026-11-13T08:30:00Z",
  acknowledged_by: null,
  resolved_by: null,
  resolution_reason: null,
  occurrence_count: 1,
  evidence_snapshot: {},
  source_anomaly_ids: [],
} as Alert;

function notification(over: Partial<Notification> = {}): Notification {
  return {
    id: "n-1",
    alert_id: "a-1",
    channel: "WEBHOOK",
    target: "SOAR",
    transition: "OPENED",
    status: "FAILED",
    attempts: 2,
    max_attempts: 5,
    next_attempt_at: "2026-11-13T10:12:00Z",
    sent_at: null,
    error_message: "Webhook returned status 503",
    attempt_log: [
      { attempted_at: "2026-11-13T10:02:00Z", http_status: 503, error_class: "http_status" },
      { attempted_at: "2026-11-13T10:00:00Z", http_status: null, error_class: "timeout" },
    ],
    ...over,
  };
}

async function renderPage() {
  return render(await AlertDetailPage({ params: Promise.resolve({ id: "a-1" }) }));
}

beforeEach(() => {
  setTestLocale("en");
  vi.restoreAllMocks();
  vi.spyOn(api, "alertPivot").mockRejectedValue(new ApiError(404, "no pivot"));
  vi.spyOn(api, "alert").mockResolvedValue(ALERT);
});

describe("delivery attempts on the alert page", () => {
  it("shows the target label and the last attempt's class, status and UTC time", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([notification()]);
    await renderPage();
    const table = screen.getByRole("table", { name: "Notification delivery history" });
    expect(within(table).getByRole("columnheader", { name: "Target" })).toBeInTheDocument();
    const row = within(table).getByText("SOAR").closest("tr") as HTMLElement;
    // The attempts sit on the row under the notification, across every column.
    const attempts = row.nextElementSibling as HTMLElement;
    expect(attempts).toHaveClass("attempt-row");
    expect(within(attempts).getByRole("cell")).toHaveAttribute("colspan", "8");
    // The visible last attempt, then the same attempt again in the closed disclosure.
    const last = within(attempts).getAllByText("http_status 503")[0];
    expect(last.closest("details")).toBeNull();
    expect(last.parentElement).toHaveTextContent("Last attempt http_status 503 11-13 10:02 UTC");
    // No separator glyph that could wrap onto a line of its own.
    expect(attempts.textContent).not.toContain("·");
  });

  it("lists every attempt, newest first, in a disclosure", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([notification()]);
    await renderPage();
    const details = screen.getByText("All attempts (2)").closest("details") as HTMLElement;
    const items = within(details).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("http_status 503");
    expect(items[1]).toHaveTextContent("timeout");
    expect(items[1]).not.toHaveTextContent("timeout null");
    expect(items[1]).toHaveTextContent("10:00 UTC");
  });

  it("lists at most eight attempts", async () => {
    const log = Array.from({ length: 10 }, (_, i) => ({
      attempted_at: `2026-11-13T10:${String(59 - i).padStart(2, "0")}:00Z`,
      http_status: 500,
      error_class: "http_status",
    }));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([notification({ attempt_log: log })]);
    await renderPage();
    const details = screen.getByText(/All attempts/).closest("details") as HTMLElement;
    expect(within(details).getAllByRole("listitem")).toHaveLength(8);
  });

  it("says when no attempt is recorded and offers no disclosure", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({ attempt_log: [], status: "PENDING", error_message: null }),
    ]);
    await renderPage();
    expect(screen.getByText("No attempt recorded")).toBeInTheDocument();
    expect(screen.queryByText(/All attempts/)).not.toBeInTheDocument();
  });

  it("treats a response without attempt_log as no attempt recorded", async () => {
    const bare = notification();
    delete bare.attempt_log;
    vi.spyOn(api, "alertNotifications").mockResolvedValue([bare]);
    await renderPage();
    expect(screen.getByText("No attempt recorded")).toBeInTheDocument();
  });

  it("shows a single attempt without a disclosure", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({
        status: "SENT",
        error_message: null,
        attempt_log: [{ attempted_at: "2026-11-13T10:02:00Z", http_status: 204, error_class: "ok" }],
      }),
    ]);
    await renderPage();
    expect(screen.getByText("ok 204")).toBeInTheDocument();
    expect(screen.queryByText(/All attempts/)).not.toBeInTheDocument();
  });

  it("breaks a target label only after :// . or @, never inside a word", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({ target: "https://hooks.corp.example" }),
    ]);
    await renderPage();
    const cell = document.querySelector("td.cell-target") as HTMLElement;
    expect(cell.textContent).toBe("https://hooks.corp.example");
    expect([...cell.querySelectorAll("wbr")]).toHaveLength(3);
    expect(cell.innerHTML).toBe("https://<wbr>hooks.<wbr>corp.<wbr>example");
  });

  it("never shows a URL", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([notification()]);
    await renderPage();
    expect(document.body.textContent).not.toMatch(/https?:\/\//);
  });
});
