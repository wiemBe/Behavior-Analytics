import type { Pivot } from "@/lib/pivot";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlertDetailPage from "./page";
import { ApiError, api, suppressionApi, type Alert, type Notification } from "@/lib/api";
import { setTestLocale } from "../../../../vitest.setup";

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
// so those are asserted. The last block asserts the honesty gap T-096 recorded (the
// lifecycle and delivery times' timezone).

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
  setTestLocale("en");
  vi.restoreAllMocks();
  vi.spyOn(api, "alertPivot").mockRejectedValue(new ApiError(404, "no pivot"));
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
  it("renders coded title, lower-bound description and resolution in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "alert").mockResolvedValue(alert({
      title: "High traffic crossed its threshold",
      title_code: "alert.threshold.crossed",
      title_params: { name: "Yüksek trafik" },
      description: "High traffic observed at least 1204 events (GT 1000 events)",
      description_code: "alert.custom_rule.description.lower_bound_unit",
      description_params: {
        name: "Yüksek trafik",
        observed: 1204,
        unit: "olay",
        operator: "GT",
        threshold: 1000,
      },
      resolution_reason: "condition recovered",
      resolution_reason_code: "alert.custom_rule.recovered",
      resolution_reason_params: {},
    }));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Yüksek trafik eşiğini aştı" })).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === (
      "Yüksek trafik, en az 1.204\u00a0olay değerini gözlemledi (eşik > 1.000\u00a0olay)"
    ))).toBeInTheDocument();
    expect(screen.getByText("Yaşam döngüsü").closest(".card")).toHaveTextContent("Çözüm: Koşul düzeldi");
  });

  it("renders a resolved WAF alert in Turkish: title, description, evidence reason and recovery", async () => {
    setTestLocale("tr");
    const description = {
      code: "waf.rate.fired",
      params: {
        observed: 520,
        expected: 100,
        ratio_text: { code: "waf.rate.ratio_multiple", params: { ratio: 5.2 } },
        delta: 420,
        basis: { code: "waf.basis.z", params: { score: 6.2, threshold: 3.5 } },
      },
    };
    vi.spyOn(api, "alert").mockResolvedValue(alert({
      status: "RESOLVED",
      title: "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}",
      title_code: "alert.waf.title",
      title_params: {
        detector: "WAF_REQUEST_RATE_SPIKE",
        log_source: "edge-waf",
        method: "POST",
        endpoint: "/login/{id}",
      },
      description: "520 requests is above the baseline median 100.0 (5.20x expected, +420 requests/bucket); robust z=6.20 (threshold +3.5)",
      description_code: description.code,
      description_params: description.params,
      resolution_reason: "condition recovered",
      resolution_reason_code: "anomaly.resolution.recovered",
      resolution_reason_params: {},
      evidence_snapshot: {
        anomaly_type: "WAF_REQUEST_RATE_SPIKE",
        reason: "520 requests is above the baseline median",
        reason_code: description.code,
        reason_params: description.params,
      },
    }));
    await renderPage();
    expect(screen.getByRole("heading", {
      name: "edge-waf üzerinde istek hızı artışı: POST /login/{id}",
    })).toBeInTheDocument();
    expect(screen.getByText("Yaşam döngüsü").closest(".card")).toHaveTextContent("Çözüm: Koşul düzeldi");
    expect(screen.queryByText(/is above the baseline median/)).not.toBeInTheDocument();
    const evidence = screen.getByText("Kanıt").closest(".card") as HTMLElement;
    expect(evidence).not.toHaveTextContent(/baseline medyanı olan/);
    expect(within(evidence).getByText("Gözlenen")).toBeInTheDocument();
    expect(screen.getAllByText(/baseline medyanı olan/)).toHaveLength(1);
  });

  it("renders a property-watch alert title in Turkish and its coded reason", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "alert").mockResolvedValue(alert({
      title: "PW_VALUE_SPIKE on Admin logins: alice | 10.0.0.1",
      title_code: "alert.pw.title",
      title_params: { detector: "PW_VALUE_SPIKE", watch: "Admin logins", value: "alice | 10.0.0.1" },
      resolution_reason: "condition recovered",
      resolution_reason_code: "pw.transition.recovered",
      resolution_reason_params: {},
      status: "RESOLVED",
    }));
    await renderPage();
    expect(screen.getByRole("heading", {
      name: "Admin logins üzerinde değer ani artışı: alice | 10.0.0.1",
    })).toBeInTheDocument();
    expect(screen.getByText("Yaşam döngüsü").closest(".card")).toHaveTextContent("Çözüm: Koşul düzeldi");
  });

  it("shows an engine alert with no refs (an old row) in its stored English", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "alert").mockResolvedValue(alert({
      title: "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}",
      status: "RESOLVED",
      resolution_reason: "condition recovered",
    }));
    await renderPage();
    expect(screen.getByRole("heading", {
      name: "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}",
    })).toBeInTheDocument();
    expect(screen.getByText("Yaşam döngüsü").closest(".card")).toHaveTextContent("Çözüm: condition recovered");
  });

  it("shows the stored English title, description and resolution verbatim", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({
      title: "Stored title",
      title_code: "alert.threshold.crossed",
      title_params: { name: "Ignored" },
      description: "Stored description",
      description_code: "alert.custom_rule.description.exact",
      description_params: { name: "Ignored", observed: 2, operator: "GT", threshold: 1 },
      resolution_reason: "Stored resolution",
      resolution_reason_code: "alert.custom_rule.recovered",
      resolution_reason_params: {},
    }));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Stored title" })).toBeInTheDocument();
    expect(screen.getByText("Stored description")).toBeInTheDocument();
    expect(screen.getByText("Lifecycle").closest(".card")).toHaveTextContent("Resolution: Stored resolution");
  });

  it("names the alert and its lifecycle at a glance", async () => {
    await renderPage();
    expect(screen.getByRole("heading", { name: "Event volume spike on Core Firewall", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toHaveClass("crit");
    expect(screen.getByText("OPEN")).toBeInTheDocument();
    expect(screen.getByText(/repeated 4× while open/)).toBeInTheDocument();
    expect(screen.getByText("vol:227:1789").tagName).toBe("CODE");
  });

  it("groups a large occurrence count", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ occurrence_count: 1200 }));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText(/repeated 1,200× while open/)).toBeInTheDocument();
  });

  it("reads a count of 1 as not repeated and never uses the old counter words", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ occurrence_count: 1 }));
    await renderPage();
    expect(screen.getByText(/not repeated/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/repeated 1×/);
    expect(document.body.textContent).not.toMatch(/seen \d|occurrence/i);
  });

  it("labels the lifecycle times without a duplicated UTC", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ last_seen_at: "2026-10-15T08:30:00Z" }));
    await renderPage();
    expect(document.body.textContent).not.toContain("bucket (UTC)");
    expect(document.body.textContent).not.toContain("evaluated (UTC)");
  });

  it("omits a repeated evidence reason and keeps a different one, in English and Turkish", async () => {
    for (const locale of ["en", "tr"] as const) {
      setTestLocale(locale);
      const evidenceLabel = locale === "tr" ? "Kanıt" : "Evidence";
      const observed = locale === "tr" ? "Gözlenen" : "Observed";
      const descriptionLabel = locale === "tr" ? "Açıklama" : "Description";
      vi.spyOn(api, "alert").mockResolvedValue(alert({
        description: "same sentence",
        evidence_snapshot: {
          anomaly_type: "VOLUME_SPIKE",
          observed_value: 5.98,
          reason: "same sentence",
        },
      }));
      const same = await renderPage();
      const evidence = screen.getByText(evidenceLabel).closest(".card") as HTMLElement;
      expect(within(evidence).queryByText("same sentence")).toBeNull();
      expect(within(evidence).getByText(observed)).toBeInTheDocument();
      expect(screen.getByText(descriptionLabel).closest(".card")).toHaveTextContent("same sentence");
      same.unmount();

      vi.spyOn(api, "alert").mockResolvedValue(alert({
        description: "the description sentence",
        evidence_snapshot: {
          anomaly_type: "VOLUME_SPIKE",
          observed_value: 5.98,
          reason: "the evidence sentence",
        },
      }));
      const different = await renderPage();
      const card = screen.getByText(evidenceLabel).closest(".card") as HTMLElement;
      expect(within(card).getByText("the evidence sentence")).toBeInTheDocument();
      expect(screen.getByText(descriptionLabel).closest(".card")).toHaveTextContent("the description sentence");
      expect(within(card).getByText(observed)).toBeInTheDocument();
      different.unmount();
    }
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

  it("shows the anomaly bucket time and the evaluation time as two different instants", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(
      alert({
        last_seen_at: "2026-10-15T08:30:00Z",
        evidence_snapshot: { observed_value: 5.98, interval_end: "2026-10-14T03:15:00Z" },
      }),
    );
    await renderPage();
    const lifecycle = screen.getByText("Lifecycle", { selector: ".k" }).closest(".card") as HTMLElement;
    expect(lifecycle).toHaveTextContent("Last anomaly bucket: 2026-10-14 03:15:00 UTC");
    expect(lifecycle).toHaveTextContent("Last evaluated: 2026-10-15 08:30:00 UTC");
  });

  it("draws a missing interval_end as a dash with its reason, never as the evaluation time", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(
      alert({ last_seen_at: "2026-10-15T08:30:00Z", evidence_snapshot: { observed_value: 5.98 } }),
    );
    await renderPage();
    const lifecycle = screen.getByText("Lifecycle", { selector: ".k" }).closest(".card") as HTMLElement;
    const bucket = within(lifecycle).getByText(/Last anomaly bucket:/).closest("p") as HTMLElement;
    expect(bucket).toHaveTextContent("— not recorded in this alert's evidence");
    expect(bucket).not.toHaveTextContent("08:30");
    expect(lifecycle).toHaveTextContent("Last evaluated: 2026-10-15 08:30:00 UTC");
  });

  it("treats an unparsable interval_end as missing", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ evidence_snapshot: { interval_end: "not a time" } }));
    await renderPage();
    const lifecycle = screen.getByText("Lifecycle", { selector: ".k" }).closest(".card") as HTMLElement;
    expect(lifecycle).toHaveTextContent("— not recorded in this alert's evidence");
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
      "Sent (UTC)",
      "Next attempt (UTC)",
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

describe("alert detail honesty", () => {
  // `toLocaleString()` rendered the lifecycle and delivery times with no timezone.
  it("renders the opened and delivery times with their timezone", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ opened_at: "2026-10-15T07:00:00Z" }));
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({
        status: "FAILED",
        sent_at: "2026-10-15T07:00:05Z",
        next_attempt_at: "2026-10-15T07:10:00Z",
      }),
    ]);
    await renderPage();
    const lifecycle = screen.getByText("Lifecycle", { selector: ".k" }).closest(".card") as HTMLElement;
    expect(lifecycle).toHaveTextContent("Opened: 2026-10-15 07:00:00 UTC");
    const row = within(screen.getAllByRole("table")[1]).getAllByRole("row")[1];
    // The headers say (UTC); the cells do not repeat it.
    expect(row).toHaveTextContent("10-15 07:00");
    expect(row).toHaveTextContent("10-15 07:10");
    expect(row).not.toHaveTextContent("UTC");
  });

  it("scrolls the delivery table in a labelled region and right-aligns the attempts", async () => {
    await renderPage();
    const region = screen.getByRole("region", { name: "Notification delivery history" });
    const row = within(region).getAllByRole("row")[1];
    expect(within(row).getByText("1/3")).toHaveClass("num");
  });

  it("carries no inline style, including in the operator actions", async () => {
    const { container } = await renderPage();
    // `Evidence` (shared, outside this task's files) still styles inline; everything else is clean.
    const styled = [...container.querySelectorAll("[style]")].filter(
      (el) => !el.closest(".alert-evidence"),
    );
    expect(styled).toEqual([]);
  });

  it("says what failed and what to do as an alert", async () => {
    vi.spyOn(api, "alert").mockRejectedValue(new ApiError(404, "secret"));
    await renderPage("a-9");
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
    expect(screen.getByRole("heading", { name: "Alert Detail", level: 1 })).toBeInTheDocument();
  });

  it("never wraps a delivery time", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({ sent_at: "2026-10-15T07:00:05Z", next_attempt_at: "2026-10-15T07:10:00Z" }),
    ]);
    await renderPage();
    const row = within(screen.getAllByRole("table")[1]).getAllByRole("row")[1];
    expect(within(row).getByText("10-15 07:00")).toHaveClass("nowrap");
    expect(within(row).getByText("10-15 07:10")).toHaveClass("nowrap");
  });

  it("has one h1, the alert title, and no h2 imitating it", async () => {
    await renderPage();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
  });

  it("keeps the error column readable: 12ch floor, may break only a token longer than the column", async () => {
    vi.spyOn(api, "alertNotifications").mockResolvedValue([
      notification({ status: "DEAD_LETTER", error_message: "channel_not_found: no longer exists" }),
    ]);
    await renderPage();
    const cell = screen.getByText("channel_not_found: no longer exists");
    expect(cell).toHaveClass("cell-error");
    const css = readFileSync(join(__dirname, "alert-detail.css"), "utf8");
    const rule = css.match(/td\.cell-error\s*{([^}]*)}/)?.[1] ?? "";
    expect(rule).toMatch(/min-width:\s*12ch/);
    expect(rule).toMatch(/overflow-wrap:\s*anywhere/);
    expect(rule).not.toMatch(/break-all|break-word/);
  });
});

const PIVOT_AQL = "SELECT 1 FROM events START 1 STOP 2";
const PIVOT: Pivot = {
  kind: "KIND",
  aql: PIVOT_AQL,
  console_url: "https://qr.lab.example/console/x",
  aql_search_url: "https://qr.lab.example/console/x",
  console_unavailable_reason: null,
  time_range: { start: "2026-11-05T10:00:00Z", stop: "2026-11-05T10:30:00Z" },
  notes: [],
};

describe("Investigate in QRadar", () => {
  it("shows the block with the server's AQL and keeps the page", async () => {

    vi.spyOn(api, "alertPivot").mockResolvedValue({ ...PIVOT, kind: "anomaly" });
    await renderPage();
    expect(screen.getByRole("heading", { name: "Investigate in QRadar" })).toBeInTheDocument();
    expect(screen.getByLabelText("AQL query")).toHaveTextContent(PIVOT_AQL);
    expect(screen.getByRole("link", { name: /Open in QRadar/ })).toHaveAttribute("target", "_blank");
  });

  it("omits the block when the object has no pivot (404)", async () => {

    vi.spyOn(api, "alertPivot").mockRejectedValue(new ApiError(404, "none"));
    await renderPage();
    expect(screen.queryByRole("heading", { name: "Investigate in QRadar" })).toBeNull();
  });

  it("says the pivot could not be built, without taking the page down", async () => {

    vi.spyOn(api, "alertPivot").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Investigate in QRadar" })).toBeInTheDocument();
    expect(screen.getByText(/could not be built/)).toBeInTheDocument();
    expect(screen.queryByText("secret")).toBeNull();
  });
});

describe("alert to anomaly links and the human title (SOC-1b)", () => {
  it("links every source anomaly", async () => {
    await renderPage();
    const link = screen.getByRole("link", { name: "Open anomaly an-1" });
    expect(link).toHaveAttribute("href", "/anomalies/an-1");
    expect(screen.getByRole("link", { name: "Open anomaly an-2" })).toHaveAttribute("href", "/anomalies/an-2");
  });

  it("shows no link list for an alert with no source anomaly", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ source_anomaly_ids: [] }));
    await renderPage();
    expect(screen.queryByRole("link", { name: /Open anomaly/ })).not.toBeInTheDocument();
  });

  it("names the detector in the page title (QW3)", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(
      alert({
        title: "VOLUME_DROP on Edge firewall",
        title_code: "alert.anomaly.title",
        title_params: { anomaly_type: "VOLUME_DROP", log_source: "Edge firewall" },
      }),
    );
    await renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Volume drop on Edge firewall");
  });
});

describe("the quiet-period block (SOC-17d-ui)", () => {
  it("reads the banner for the first source anomaly of an anomaly alert", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ source_type: "waf_endpoint" }));
    const read = vi.spyOn(suppressionApi, "forAnomaly").mockRejectedValue(new ApiError(500, "x"));
    await renderPage();
    await waitFor(() => expect(read).toHaveBeenCalledWith("waf", "an-1"));
  });

  it("carries no block for an alert that has no detector condition", async () => {
    const read = vi.spyOn(suppressionApi, "forAnomaly");
    await renderPage();
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByText(/Don't alert this again/)).toBeNull();
  });

  it("carries no block for an anomaly alert with no anomaly to read the condition from", async () => {
    vi.spyOn(api, "alert").mockResolvedValue(alert({ source_type: "log_source", source_anomaly_ids: [] }));
    const read = vi.spyOn(suppressionApi, "forAnomaly");
    await renderPage();
    expect(read).not.toHaveBeenCalled();
  });
});
