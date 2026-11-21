import type { Pivot } from "@/lib/pivot";
import { render, screen, within } from "@testing-library/react";
import { renderToReadableStream } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LogSourceDetailPage from "./page";
import {
  ApiError,
  JDBC_PROTOCOL_IDS,
  JDBC_SAFE_FIELDS,
  api,
  fleetApi,
  type JdbcSafeFields,
  type JdbcSourceDetail,
  type LogSourceDetail,
} from "@/lib/api";
import en from "../../../../messages/en";
import { GUIDE_SECTIONS } from "@/lib/guideSections";

// States this route renders, from `page.tsx`:
//  - the read fails: 401 session expired, 403 names read:log_sources, 404 is not
//    in the inventory, and any other failure says the backend could not be reached;
//  - the normal detail: the header line, the five-part health breakdown, the
//    configuration card and the Phase-2 chart placeholder;
//  - a null health breakdown: the breakdown cards are simply absent;
//  - honesty: a missing type reads "unknown type", a missing owner / description /
//    expected interval is an em dash, a source that has never sent an event says
//    "never", and a missing QRadar status reads "unknown".
// The last block asserts the honesty gaps T-096 recorded (the last-event timezone).

function detail(over: Partial<LogSourceDetail> = {}): LogSourceDetail {
  return {
    id: "s-1",
    qradar_id: 227,
    enabled: true,
    monitoring_enabled: true,
    maintenance_mode: false,
    timezone_name: "UTC",
    name: "Core Firewall",
    type_name: "Check Point Firewall",
    description: "Perimeter firewall",
    criticality: "HIGH",
    owner: "SOC",
    owner_email: "soc@example.com",
    qradar_status: "SUCCESS",
    business_hours_only: false,
    expected_interval_seconds: 300,
    last_event_time: "2026-10-15T08:59:00Z",
    ...over,
  };
}

async function renderPage(id = "s-1") {
  return render(await LogSourceDetailPage({ params: Promise.resolve({ id }) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "logSourcePivot").mockRejectedValue(new ApiError(404, "no pivot"));
  vi.spyOn(fleetApi, "sourceClass").mockRejectedValue(new ApiError(500, "no class"));
});

it("still renders the page and says the class could not be read", async () => {
  vi.spyOn(api, "logSource").mockResolvedValue(detail());
  await renderPage();
  expect(screen.getByRole("heading", { name: "Core Firewall" })).toBeInTheDocument();
  expect(screen.getByText(/Class could not be read/)).toBeInTheDocument();
});

describe("log source detail states", () => {
  it("says the backend could not be reached on a non-auth failure", async () => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage("s-9");
    expect(screen.getByRole("heading", { name: "Log Source Detail" })).toBeInTheDocument();
    const notice = screen.getByRole("alert");
    expect(notice).toHaveTextContent("The backend could not be reached. Reload the page to try again.");
    expect(notice).not.toHaveTextContent("secret");
  });

  it("shows the header line with type, criticality and owner", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    await renderPage();
    expect(screen.getByRole("heading", { name: "Core Firewall", level: 1 })).toBeInTheDocument();
    expect(
      screen.getByText("Check Point Firewall · criticality HIGH · owner SOC"),
    ).toBeInTheDocument();
  });
});

describe("log source detail contents", () => {
  beforeEach(() => vi.spyOn(api, "logSource").mockResolvedValue(detail()));

  it("has no health section: no score is computed, so none is promised", async () => {
    await renderPage();
    expect(screen.queryByText(/health/i)).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("Configuration", { selector: ".k" })).toBeInTheDocument();
  });

  it("shows the configuration in the order it is read", async () => {
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Description: Perimeter firewall");
    expect(config).toHaveTextContent("Business hours only: no");
    expect(config).toHaveTextContent("Expected interval: 300 s");
    expect(config).toHaveTextContent("QRadar status: SUCCESS");
  });

  it("links to the charts instead of promising them", async () => {
    await renderPage();
    expect(screen.queryByText(/arrive in Phase 2/)).toBeNull();
    expect(screen.getByRole("link", { name: "Source behavior" })).toHaveAttribute(
      "href",
      "/behavior/sources/s-1",
    );
  });

  it("draws missing values as unknown, never, or an em dash", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(
      detail({
        type_name: null,
        owner: null,
        description: null,
        expected_interval_seconds: null,
        last_event_time: null,
        qradar_status: null,
        business_hours_only: true,
      }),
    );
    await renderPage();
    expect(screen.getByText(/unknown type · criticality HIGH · owner —/)).toBeInTheDocument();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Description: —");
    expect(config).toHaveTextContent("Business hours only: yes");
    expect(config).toHaveTextContent(/Expected interval: —Last event/);
    expect(config).toHaveTextContent("Last event: never");
    expect(config).toHaveTextContent("QRadar status: unknown");
  });
});

describe("log source detail honesty", () => {
  // `toLocaleString()` rendered the viewer's zone with no zone name.
  it("renders the last-event time with its timezone", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(
      detail({ last_event_time: "2026-10-15T08:59:00Z" }),
    );
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("Last event: 2026-10-15 08:59:00 UTC");
  });

  it("carries no inline style (the page moved them to a stylesheet)", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    const { container } = await renderPage();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it.each([
    [401, "Your session has expired. Sign in again to continue."],
    [403, "Ask an administrator for the read:log_sources grant."],
    [404, "Log source s-9 is not in the inventory."],
    [500, "The backend could not be reached. Reload the page to try again."],
  ])("splits a %s read into its own sentence", async (status, sentence) => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(status, "secret"));
    await renderPage("s-9");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(sentence);
    expect(alert).not.toHaveTextContent("secret");
    if (status === 404) expect(alert).not.toHaveTextContent("Reload the page");
  });

  it("gives a missing expected interval no unit", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ expected_interval_seconds: null }));
    await renderPage();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config.textContent).toContain("Expected interval: —");
    expect(config.textContent).not.toMatch(/—\s*s/);
  });

  it.each([
    ["SUCCESS", "ok"],
    ["WARNING", "warn"],
    ["ERROR", "crit"],
  ])("tones the QRadar status %s as %s", async (status, tone) => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ qradar_status: status }));
    await renderPage();
    expect(screen.getByText(status, { selector: ".pill" })).toHaveClass(tone);
  });

  it.each(["DISABLED", "NA", null])("keeps the status %s neutral", async (status) => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ qradar_status: status }));
    await renderPage();
    const pill = screen.getByText(status ?? "unknown", { selector: ".pill" });
    for (const t of ["ok", "warn", "crit"]) expect(pill).not.toHaveClass(t);
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
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    vi.spyOn(api, "logSourcePivot").mockResolvedValue({ ...PIVOT, kind: "log_source" });
    await renderPage();
    expect(screen.getByRole("heading", { name: "Investigate in QRadar" })).toBeInTheDocument();
    expect(screen.getByLabelText("AQL query")).toHaveTextContent(PIVOT_AQL);
    expect(screen.getByRole("link", { name: /Open in QRadar/ })).toHaveAttribute("target", "_blank");
  });

  it("omits the block when the object has no pivot (404)", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    vi.spyOn(api, "logSourcePivot").mockRejectedValue(new ApiError(404, "none"));
    await renderPage();
    expect(screen.queryByRole("heading", { name: "Investigate in QRadar" })).toBeNull();
  });

  it("says the pivot could not be built, without taking the page down", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    vi.spyOn(api, "logSourcePivot").mockRejectedValue(new ApiError(500, "secret"));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Investigate in QRadar" })).toBeInTheDocument();
    expect(screen.getByText(/could not be built/)).toBeInTheDocument();
    expect(screen.queryByText("secret")).toBeNull();
  });
});

/** The JDBC block is an async child of a Suspense boundary: stream it to the end, then mount the HTML. */
const streamed: HTMLElement[] = [];
afterEach(() => streamed.splice(0).forEach((host) => host.remove()));

async function renderStreamed(id = "s-1") {
  const stream = await renderToReadableStream(
    await LogSourceDetailPage({ params: Promise.resolve({ id }) }),
  );
  await stream.allReady;
  const html = await new Response(stream).text();
  const host = document.body.appendChild(document.createElement("div"));
  host.innerHTML = html;
  streamed.push(host);
  return { container: host, unmount: () => host.remove() };
}

// JDBC block (J1p2): requested only for the JDBC protocols, read on its own, mounted beside the
// metadata (never inside its card), and explained by one guide entry.
function jdbcSource(): JdbcSourceDetail {
  const at = "2026-11-05T18:00:00Z";
  return {
    id: "s-1", qradar_id: 7, name: "Billing DB", protocol_type_id: 8, protocol_name: "JDBC",
    qradar: {
      enabled: true, status: "WARNING", inconsistency: null, inventory_last_complete_at: at,
      inventory_last_attempt_at: at, inventory_state: "COMPLETE", inventory_stale: false,
      reason_code: null,
    },
    config: {
      support: "VERIFIED", state: "COMPLETE", failure_code: null, observed_at: at,
      last_success_at: at, stale: false,
      safe_fields: {
        ...Object.fromEntries(JDBC_SAFE_FIELDS.map((n) => [n, null])),
        database_type: "MSSQL", database_name: "billing", start_time_local: "2026-11-01 03:00:00",
      } as JdbcSafeFields,
      field_states: Object.fromEntries(JDBC_SAFE_FIELDS.map((n) => [n, { code: "SET", reason: null }])),
    },
    volume: {
      measurement: "EXACT", target_start: at, target_end: at, bucket_seconds: 3600, event_count: 120,
      average_eps: 0.033, collected_at: at, stale: false, reason_code: "measured", last_observation: null,
    },
    flow: { state: "RECEIVING", reason_code: "measured" }, monitoring: { enabled: true },
  };
}

describe("JDBC block on the source detail", () => {
  it("only JDBC protocols request block", async () => {
    const jdbc = vi.spyOn(api, "jdbcSource").mockResolvedValue(jdbcSource());
    for (const protocol of [undefined, null, 1, 9, 12]) {
      vi.spyOn(api, "logSource").mockResolvedValue(detail({ protocol_type_id: protocol } as never));
      const view = await renderStreamed();
      expect(screen.queryByRole("region", { name: "JDBC details" })).toBeNull();
      view.unmount();
    }
    expect(jdbc).not.toHaveBeenCalled();
    for (const protocol of JDBC_PROTOCOL_IDS) {
      vi.spyOn(api, "logSource").mockResolvedValue(detail({ protocol_type_id: protocol } as never));
      const view = await renderStreamed();
      view.unmount();
    }
    expect(jdbc).toHaveBeenCalledTimes(JDBC_PROTOCOL_IDS.length);
    expect(jdbc).toHaveBeenCalledWith("s-1");
  });

  it("loading: a block that never answers leaves the metadata and a loading status", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ protocol_type_id: 8 } as never));
    vi.spyOn(api, "jdbcSource").mockReturnValue(new Promise(() => {}));
    await renderPage();
    expect(screen.getByRole("heading", { name: "Core Firewall", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Source behavior")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading JDBC details…");
    expect(screen.queryByRole("region", { name: "Connection" })).toBeNull();
  });

  it("shows the block, with WARN as QRadar's status and the start time zone unknown", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ protocol_type_id: 8 } as never));
    vi.spyOn(api, "jdbcSource").mockResolvedValue(jdbcSource());
    await renderStreamed();
    const block = screen.getByRole("region", { name: "JDBC details" });
    expect(within(block).getByRole("region", { name: "Connection" })).toHaveTextContent("Warning");
    expect(block).toHaveTextContent("Timezone unknown");
    expect(within(block).getByRole("link", { name: "How to read the JDBC details" })).toHaveAttribute(
      "href",
      "/guide/log-sources",
    );
  });

  it.each([
    [403, "JDBC details require read:log_sources."],
    [404, "This source has no JDBC details in the inventory."],
    [500, "JDBC details could not be loaded. Reload the page to retry."],
  ])("failure leaves metadata readable (%s)", async (status, sentence) => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ protocol_type_id: 8 } as never));
    vi.spyOn(api, "jdbcSource").mockRejectedValue(new ApiError(status, "secret"));
    await renderStreamed();
    expect(screen.getByRole("heading", { name: "Core Firewall", level: 1 })).toBeInTheDocument();
    const config = screen.getByText("Configuration", { selector: ".k" }).closest(".card")!;
    expect(config).toHaveTextContent("QRadar status: SUCCESS");
    expect(screen.getByRole("region", { name: "JDBC details" })).toHaveTextContent(sentence);
    expect(screen.queryByText(/secret/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("no nested card", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail({ protocol_type_id: 8 } as never));
    vi.spyOn(api, "jdbcSource").mockResolvedValue(jdbcSource());
    const { container } = await renderStreamed();
    const block = screen.getByRole("region", { name: "JDBC details" });
    expect(block.closest(".card")).toBeNull();
    expect(block.querySelector(".card")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
    const levels = [...container.querySelectorAll("h1, h2, h3")].map((h) => h.tagName);
    expect(levels[0]).toBe("H1");
    expect(levels.filter((l) => l === "H1")).toHaveLength(1);
  });

  it("guide explains attribution and WARN", () => {
    const section = en.guide.sections["log-sources"];
    const text = JSON.stringify(section);
    expect(text).toMatch(/counted by log-source identity, not by the JDBC table/);
    expect(text).toMatch(/\*\*Warning\*\* is QRadar's connection status/);
    expect(text).toMatch(/not a verdict on whether events flow/);
    expect(text).toContain("**Timezone unknown**");
    expect(GUIDE_SECTIONS.find((g) => g.id === "log-sources")!.routes).toContain("/log-sources/jdbc");
  });
});
