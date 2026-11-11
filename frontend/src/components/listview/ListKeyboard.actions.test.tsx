// SOC-15b: `a` acknowledges the marked alert, `n` opens the marked anomaly at its note field.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "@/app/alerts/page";
import AnomaliesPage from "@/app/anomalies/page";
import { TriagePanel } from "@/components/triage/TriagePanel";
import { ApiError, api, type Alert, type AnomalySummary, type Triage } from "@/lib/api";

const router = { push: vi.fn(), refresh: vi.fn() };
const withRouter = (ui: React.ReactNode) => (
  <AppRouterContext.Provider value={router as never}>{ui}</AppRouterContext.Provider>
);

const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) =>
  act(() => {
    fireEvent.keyDown(target, { key, ...init });
  });
const flush = () => act(async () => {});
const marked = () =>
  Array.from(document.querySelectorAll("tbody tr[data-kbd-row]")).map((r) => r.textContent?.trim());
const result = () => document.querySelector(".list-keys-result")!;

function alert(id: string, title: string, status = "OPEN"): Alert {
  return {
    id,
    fingerprint: `vol:${id}`,
    title,
    description: null,
    severity: "HIGH",
    status,
    source_type: "ANOMALY",
    opened_at: "2026-10-15T07:00:00Z",
    first_seen_at: "2026-10-15T07:00:00Z",
    last_seen_at: "2026-10-15T08:30:00Z",
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 4,
    evidence_snapshot: {},
    source_anomaly_ids: ["an-1"],
  } as Alert;
}

function anomaly(id: string, name: string): AnomalySummary {
  return {
    id,
    log_source_id: `11111111-1111-1111-1111-11111111111${id.slice(-1)}`,
    log_source_name: name,
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
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  router.push.mockReset();
  router.refresh.mockReset();
  vi.spyOn(api, "logSources").mockResolvedValue([]);
});
afterEach(() => {
  document.body.innerHTML = "";
});

function stubAlerts(items: Alert[]) {
  vi.spyOn(api, "alerts").mockResolvedValue({ items, total: items.length, limit: 100, offset: 0 });
}
const alertsPage = async () =>
  withRouter(await AlertsPage({ searchParams: Promise.resolve({}) }));

describe("a on /alerts", () => {
  it("acknowledges the marked open alert once and announces only after the backend answered", async () => {
    stubAlerts([alert("al-1", "First alert"), alert("al-2", "Second alert")]);
    let finish: (a: Alert) => void = () => {};
    const ack = vi
      .spyOn(api, "acknowledgeAlert")
      .mockReturnValue(new Promise<Alert>((r) => (finish = r)));
    render(await alertsPage());
    press("j");
    press("j");
    press("a");
    expect(ack).toHaveBeenCalledTimes(1);
    expect(ack).toHaveBeenCalledWith("al-2");
    expect(result().textContent).toBe("");
    finish(alert("al-2", "Second alert", "ACKNOWLEDGED"));
    await flush();
    expect(result().textContent).toBe("Alert acknowledged: Second alert.");
    expect(router.refresh).toHaveBeenCalledTimes(1);
  });

  it("ignores a second a while the call runs", async () => {
    stubAlerts([alert("al-1", "First alert")]);
    let finish: (a: Alert) => void = () => {};
    const ack = vi
      .spyOn(api, "acknowledgeAlert")
      .mockReturnValue(new Promise<Alert>((r) => (finish = r)));
    render(await alertsPage());
    press("j");
    press("a");
    press("a");
    expect(ack).toHaveBeenCalledTimes(1);
    finish(alert("al-1", "First alert", "ACKNOWLEDGED"));
    await flush();
    // Once answered, a is live again.
    press("a");
    expect(ack).toHaveBeenCalledTimes(2);
    finish(alert("al-1", "First alert", "ACKNOWLEDGED"));
    await flush();
  });

  it.each(["ACKNOWLEDGED", "RESOLVED"])("calls nothing on a %s alert and says why", async (status) => {
    stubAlerts([alert("al-1", "First alert", status)]);
    const ack = vi.spyOn(api, "acknowledgeAlert").mockResolvedValue(alert("al-1", "x"));
    render(await alertsPage());
    press("j");
    press("a");
    await flush();
    expect(ack).not.toHaveBeenCalled();
    expect(result().textContent).toBe("Only an open alert can be acknowledged.");
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("does nothing before a row is marked", async () => {
    stubAlerts([alert("al-1", "First alert")]);
    const ack = vi.spyOn(api, "acknowledgeAlert").mockResolvedValue(alert("al-1", "x"));
    render(await alertsPage());
    press("a");
    expect(ack).not.toHaveBeenCalled();
    expect(result().textContent).toBe("");
  });

  it("shows the alert:ack refusal of a 403 and no success text", async () => {
    stubAlerts([alert("al-1", "First alert")]);
    const ack = vi.spyOn(api, "acknowledgeAlert").mockRejectedValue(
      new ApiError(403, "Missing permission", "Missing permission", {
        detail: "Missing permission",
        detail_code: "auth.permission.missing",
        detail_params: { permission: "alert:ack" },
      }),
    );
    render(await alertsPage());
    press("j");
    press("a");
    await flush();
    expect(result().textContent).toBe("You need the alert:ack permission for this.");
    expect(result().textContent).not.toMatch(/acknowledged/);
    expect(router.refresh).not.toHaveBeenCalled();
    // A failure frees the key.
    expect(ack).toHaveBeenCalledTimes(1);
    ack.mockResolvedValue(alert("al-1", "x"));
    press("a");
    expect(ack).toHaveBeenCalledTimes(2);
    await flush();
  });

  it("keeps the mark on the same alert after the list is read again", async () => {
    stubAlerts([alert("al-1", "First alert"), alert("al-2", "Second alert")]);
    vi.spyOn(api, "acknowledgeAlert").mockResolvedValue(alert("al-2", "x", "ACKNOWLEDGED"));
    const view = render(await alertsPage());
    press("j");
    press("j");
    press("a");
    await flush();
    // The refresh brings a new first row and the status of the acknowledged one.
    view.rerender(
      withRouter(
        await (async () => {
          stubAlerts([
            alert("al-0", "Newer alert"),
            alert("al-1", "First alert"),
            alert("al-2", "Second alert", "ACKNOWLEDGED"),
          ]);
          return AlertsPage({ searchParams: Promise.resolve({}) });
        })(),
      ),
    );
    expect(marked()).toHaveLength(1);
    expect(marked()[0]).toMatch(/^Second alert/);
    expect(document.querySelector("tr[data-kbd-row]")!.getAttribute("data-alert-status")).toBe(
      "ACKNOWLEDGED",
    );
  });

  it("is inert in an input and with a modifier", async () => {
    stubAlerts([alert("al-1", "First alert")]);
    const ack = vi.spyOn(api, "acknowledgeAlert").mockResolvedValue(alert("al-1", "x"));
    render(await alertsPage());
    press("j");
    press("a", {}, document.querySelector("input, select")!);
    press("a", { ctrlKey: true });
    press("a", { metaKey: true });
    press("a", { altKey: true });
    expect(ack).not.toHaveBeenCalled();
  });

  it("lists a and the g sequences in the hint, and not n", async () => {
    stubAlerts([alert("al-1", "First alert")]);
    render(await alertsPage());
    const hint = document.querySelector(".list-keys")!.textContent!;
    expect(hint).toMatch(/a\s*acknowledge/);
    expect(hint).not.toMatch(/add a note/);
    expect(hint).toMatch(/Go to\s*g\s*d\s*Dashboard\s*g\s*q\s*Queue\s*g\s*a\s*Anomalies\s*g\s*l\s*Alerts\s*g\s*o\s*Offenses/);
  });

  it("does nothing on n", async () => {
    stubAlerts([alert("al-1", "First alert")]);
    render(await alertsPage());
    press("j");
    press("n");
    expect(router.push).not.toHaveBeenCalled();
  });
});

describe("n on /anomalies", () => {
  beforeEach(() => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    const items = [anomaly("a-1", "Alpha Firewall"), anomaly("a-2", "Beta Proxy")];
    vi.spyOn(api, "anomalies").mockResolvedValue({ items, total: 2, limit: 25, offset: 0 });
  });
  const page = async () =>
    withRouter(await AnomaliesPage({ searchParams: Promise.resolve({}) }));

  it("opens the marked row's detail at the note anchor", async () => {
    render(await page());
    press("j");
    press("j");
    press("n");
    expect(router.push).toHaveBeenCalledTimes(1);
    expect(router.push).toHaveBeenCalledWith("/anomalies/a-2#triage-note");
  });

  it("does nothing before a row is marked, in an input, and with a modifier", async () => {
    render(await page());
    press("n");
    press("j");
    press("n", {}, document.querySelector("input, select")!);
    press("n", { ctrlKey: true });
    press("n", { metaKey: true });
    expect(router.push).not.toHaveBeenCalled();
  });

  it("does nothing on a, and the hint lists n and not a", async () => {
    render(await page());
    press("j");
    press("a");
    expect(router.push).not.toHaveBeenCalled();
    const hint = document.querySelector(".list-keys")!.textContent!;
    expect(hint).toMatch(/n\s*add a note/);
    expect(hint).not.toMatch(/acknowledge/);
  });
});

describe("the triage panel on #triage-note", () => {
  const triage: Triage = {
    family: "log_source",
    anomaly_id: "a-1",
    status: "NEW",
    owner: null,
    disposition: null,
    disposition_note: null,
    updated_by: null,
    updated_at: null,
    version: 0,
    events: { items: [], total: 0, limit: 50, offset: 0 },
  } as Triage;
  const panel = (access: "write" | "read" = "write") =>
    render(
      <TriagePanel family="log_source" anomalyId="a-1" initial={triage} access={access} me="x" />,
    );
  afterEach(() => {
    window.location.hash = "";
  });

  it("focuses the comment field when the hash is present", () => {
    window.location.hash = "#triage-note";
    panel();
    expect(screen.getByRole("textbox", { name: /comment/i })).toHaveFocus();
  });

  it("leaves focus alone without the hash", () => {
    panel();
    expect(document.body).toHaveFocus();
  });

  it("invents no field for a viewer without write access", () => {
    window.location.hash = "#triage-note";
    panel("read");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.body).toHaveFocus();
  });
});
