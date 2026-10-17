import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { wafListItem } from "@/components/waf/fixtures";
import { ApiError, api, type AnomalySummary, type Page } from "@/lib/api";

function phaseA(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    ...wafListItem(),
    id: "a-1",
    subject_kind: "LOG_SOURCE",
    waf_endpoint: null,
    observed_measurement: null,
    log_source_name: "LAB Firewall",
    anomaly_type: "VOLUME_SPIKE",
    observed_value: 6,
    expected_value: 2,
    ...over,
  };
}

function page(items: AnomalySummary[]): Page<AnomalySummary> {
  return { items, total: items.length, limit: 25, offset: 0 };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  params: Record<string, string | undefined> = {},
  items: AnomalySummary[] = [phaseA(), wafListItem()],
) {
  const list = vi.spyOn(api, "anomalies").mockResolvedValue(page(items));
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
  const view = render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
  return { ...view, list };
}

const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());

describe("a list with both kinds", () => {
  it("renders a WAF subject as method, path and source, and a Phase A subject as its source", async () => {
    await renderPage();
    expect(screen.getByText("GET /portal/login")).toBeInTheDocument();
    const wafRow = screen.getByText("GET /portal/login").closest("tr")!;
    expect(wafRow).toHaveTextContent("GET /portal/login · lab-waf-behavior-01");
    expect(within(wafRow).getByRole("link", { name: "lab-waf-behavior-01" })).toHaveAttribute(
      "href",
      "/behavior/waf/sources/5a1f0000-0000-4000-8000-000000000312",
    );
    expect(screen.getByRole("link", { name: "LAB Firewall" })).toHaveAttribute(
      "href",
      "/behavior/sources/5a1f0000-0000-4000-8000-000000000312",
    );
  });

  it("names the WAF detector as a word and shows the virtual host beneath the subject", async () => {
    await renderPage();
    const row = screen.getByText("GET /portal/login").closest("tr")!;
    expect(within(row).getByText("Request-rate spike")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("WAF_REQUEST_RATE_SPIKE");
    expect(screen.getByText("Virtual host portal.lab.example")).toBeInTheDocument();
  });

  it("heads the first column Subject once a WAF endpoint is on the page, and drops the EPS unit", async () => {
    await renderPage();
    expect(headers()[0]).toBe("Subject");
    expect(headers()[3]).toBe("Observed → Expected");
  });

  it("heads the first column Subject unconditionally, and keeps the EPS unit on a page of log sources only", async () => {
    await renderPage({}, [phaseA()]);
    expect(headers()[0]).toBe("Subject");
    expect(headers()[3]).toBe("Observed → Expected (EPS)");
  });

  it("states each WAF row's unit in its cell, and a lower bound as ≥", async () => {
    await renderPage({}, [
      wafListItem({ id: "v", observed_measurement: "LOWER_BOUND" }),
      wafListItem({
        id: "r",
        anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
        observed_value: 0.318,
        expected_value: 0.021,
        waf_endpoint: { ...wafListItem().waf_endpoint!, normalized_endpoint: "/api/x" },
      }),
    ]);
    const volume = screen.getByText("GET /portal/login").closest("tr")!;
    expect(volume).toHaveTextContent("≥ 412");
    expect(volume).toHaveTextContent("41.5 req");
    const ratio = screen.getByText("GET /api/x").closest("tr")!;
    expect(ratio).toHaveTextContent("31.8%");
    expect(ratio).toHaveTextContent("2.1%");
    expect(ratio).not.toHaveTextContent("req");
  });

  it("never turns a missing WAF observed value into zero", async () => {
    await renderPage({}, [wafListItem({ observed_value: null, expected_value: null, observed_measurement: null })]);
    const row = screen.getByText("GET /portal/login").closest("tr")!;
    expect(row).toHaveTextContent("— → —");
    expect(row).not.toHaveTextContent(/\b0\b.*req/);
  });
});

describe("the subject filter", () => {
  it("offers all subjects, log sources and WAF endpoints", async () => {
    await renderPage();
    const select = screen.getByLabelText("Subject kind") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "All subjects",
      "Log sources",
      "WAF endpoints",
      "IPS entities",
      "IPS sources",
      "Property watch values",
    ]);
    expect(select.name).toBe("subject_kind");
  });

  it("sends subject_kind to the API and shows it as a removable chip", async () => {
    const { list } = await renderPage({ subject_kind: "WAF_ENDPOINT" }, [wafListItem()]);
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ subject_kind: "WAF_ENDPOINT" }));
    expect((screen.getByLabelText("Subject kind") as HTMLSelectElement).value).toBe("WAF_ENDPOINT");
    expect(screen.getByRole("link", { name: /Subject: WAF endpoints/ })).toHaveAttribute(
      "href",
      "/anomalies",
    );
  });

  it("does not send a subject_kind it does not recognise", async () => {
    const { list } = await renderPage({ subject_kind: "NOPE" });
    expect(list.mock.calls[0][0]).not.toHaveProperty("subject_kind", "NOPE");
    expect(list.mock.calls[0][0]?.subject_kind).toBeUndefined();
  });

  it("sends a WAF detector type from anomaly_type, which the Phase A parser would drop", async () => {
    const { list } = await renderPage({ anomaly_type: "WAF_BLOCK_RATIO_SPIKE" });
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ anomaly_type: "WAF_BLOCK_RATIO_SPIKE" }));
    expect(screen.getByRole("link", { name: /Detector: Block-ratio spike/ })).toBeInTheDocument();
  });

  it("still sends a Phase A detector type", async () => {
    const { list } = await renderPage({ anomaly_type: "VOLUME_DROP" });
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ anomaly_type: "VOLUME_DROP" }));
  });

  it("keeps the subject filter across pagination", async () => {
    vi.spyOn(api, "anomalies").mockResolvedValue({ items: [wafListItem()], total: 60, limit: 25, offset: 0 });
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    render(await AnomaliesPage({ searchParams: Promise.resolve({ subject_kind: "WAF_ENDPOINT" }) }));
    expect(screen.getByRole("link", { name: "Next" }).getAttribute("href")).toContain(
      "subject_kind=WAF_ENDPOINT",
    );
  });
});

describe("states", () => {
  it("says a filtered empty list is a filtered view, not a statement about the fleet", async () => {
    await renderPage({ subject_kind: "WAF_ENDPOINT" }, []);
    expect(screen.getByText(/No anomalies match these filters/)).toBeInTheDocument();
  });

  it("says what failed and does not blank the filters", async () => {
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(500, "boom"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.getByLabelText("Subject kind")).toBeInTheDocument();
  });

  it("names a WAF subject in the row's disclosure control", async () => {
    await renderPage({}, [wafListItem()]);
    expect(
      screen.getByRole("button", {
        name: "Show technical detail for the request-rate spike on GET /portal/login · lab-waf-behavior-01",
      }),
    ).toBeInTheDocument();
  });
});

describe("units in the mixed list", () => {
  it("names the unit in every cell, whatever the filter: EPS, req / 5 min and %", async () => {
    await renderPage({}, [
      phaseA(),
      wafListItem({ id: "v" }),
      wafListItem({
        id: "r",
        anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
        observed_value: 0.318,
        expected_value: 0.021,
        waf_endpoint: { ...wafListItem().waf_endpoint!, normalized_endpoint: "/api/x" },
      }),
    ]);
    expect(screen.getByRole("link", { name: "LAB Firewall" }).closest("tr")).toHaveTextContent(
      "6.00 → 2.00 EPS",
    );
    expect(screen.getByText("GET /portal/login").closest("tr")).toHaveTextContent(
      "412 → 41.5 req / 5 min",
    );
    expect(screen.getByText("GET /api/x").closest("tr")).toHaveTextContent("31.8% → 2.1%");
  });

  it("keeps the Phase A unit when the list is filtered to log sources", async () => {
    await renderPage({ subject_kind: "LOG_SOURCE" }, [phaseA()]);
    expect(screen.getByRole("link", { name: "LAB Firewall" }).closest("tr")).toHaveTextContent("EPS");
  });
});

describe("the subject cell", () => {
  it("stacks the endpoint, the source link and the virtual host, with the full source name as title", async () => {
    await renderPage();
    const cell = screen.getByText("GET /portal/login").closest("td")!;
    const lines = Array.from(cell.children).filter(
      (c) => !c.classList.contains("sr-only") && !c.classList.contains("cell-fold"),
    );
    expect(lines.map((c) => c.textContent)).toEqual([
      "GET /portal/login",
      "lab-waf-behavior-01",
      "Virtual host portal.lab.example",
    ]);
    const link = within(cell).getByRole("link", { name: "lab-waf-behavior-01" });
    expect(link).toHaveAttribute("title", "lab-waf-behavior-01");
    expect(link.className).toContain("subject-source");
    // Below 1100 px the Started column folds in as one more line.
    expect(within(cell).getByText(/^Started 10-12 09:00/).className).toContain("fold-sm");
  });

  it("omits the virtual host line when there is none", async () => {
    await renderPage({}, [
      wafListItem({ waf_endpoint: { ...wafListItem().waf_endpoint!, virtual_host: null } }),
    ]);
    expect(screen.queryByText(/Virtual host/)).not.toBeInTheDocument();
  });
});

describe("evidence in the list", () => {
  it("shows a word for a Phase A row, not the enum", async () => {
    await renderPage({}, [phaseA({ evidence_status: "NOT_REQUESTED" })]);
    const row = screen.getByRole("link", { name: "LAB Firewall" }).closest("tr")!;
    expect(within(row).getAllByText("Not requested").length).toBeGreaterThan(0);
    expect(row).not.toHaveTextContent("NOT_REQUESTED");
  });

  it("shows a dash with its meaning for a WAF row, and never 'Not requested'", async () => {
    await renderPage({}, [wafListItem({ evidence_status: "NOT_REQUESTED" })]);
    const row = screen.getByText("GET /portal/login").closest("tr")!;
    expect(row).not.toHaveTextContent(/not requested/i);
    expect(within(row).getByTitle("Evidence collection does not apply to WAF anomalies")).toHaveTextContent("—");
    expect(row).toHaveTextContent("Evidence collection does not apply to WAF anomalies");
  });
});

describe("the narrow-width folds", () => {
  it("keeps Evidence and Deviation reachable as columns, and folds each under its neighbour", async () => {
    await renderPage({}, [phaseA({ evidence_status: "PARTIAL", deviation_ratio: 3.05 })]);
    const row = screen.getByRole("link", { name: "LAB Firewall" }).closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    // Columns stay; CSS hides them at ≤ 1280 px and shows the folded copy.
    expect(cells[4].className).toContain("col-md");
    expect(cells[7].className).toContain("col-md");
    const evidenceFold = within(cells[2]).getByText("Evidence: Partial");
    expect(evidenceFold.className).toContain("fold-md");
    const deviationFold = within(cells[3]).getByText("Deviation 3.05x");
    expect(deviationFold.className).toContain("fold-md");
    // Below 1100 px severity folds under the detector.
    expect(cells[5].className).toContain("col-sm");
    expect(within(cells[1]).getByText(/Severity HIGH/).className).toContain("fold-sm");
  });

  it("folds no evidence line into a WAF row: there is none to fold", async () => {
    await renderPage({}, [wafListItem()]);
    expect(screen.queryByText(/^Evidence:/)).not.toBeInTheDocument();
  });
});

describe("the filter chip", () => {
  it("has an accessible name that says what removing it does", async () => {
    await renderPage({ subject_kind: "LOG_SOURCE" }, [phaseA()]);
    const chip = screen.getByRole("link", { name: "Remove filter Subject: Log sources" });
    expect(chip).toHaveAttribute("href", "/anomalies");
    expect(chip.textContent).toBe("Subject: Log sources✕");
  });
});

describe("a new endpoint row", () => {
  it("reads the cumulative count with its own unit and none expected, not a per-bucket rate", async () => {
    await renderPage({}, [
      wafListItem({
        anomaly_type: "WAF_NEW_ENDPOINT_ACCESS",
        observed_value: 320,
        expected_value: 0,
        deviation_ratio: null,
        robust_z: null,
        absolute_delta: 320,
      }),
    ]);
    const row = screen.getByText("GET /portal/login").closest("tr")!;
    expect(row).toHaveTextContent("320 → none");
    expect(row).toHaveTextContent("requests since it first appeared");
    expect(row).not.toHaveTextContent("req / 5 min");
    expect(row).not.toHaveTextContent("→ 0");
  });
});
