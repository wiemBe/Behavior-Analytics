import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { ipsListItem } from "@/components/ips/fixtures";
import { wafListItem } from "@/components/waf/fixtures";
import { api, type AnomalySummary, type Page } from "@/lib/api";

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
const page = (items: AnomalySummary[]): Page<AnomalySummary> => ({
  items,
  total: items.length,
  limit: 25,
  offset: 0,
});

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  params: Record<string, string | undefined> = {},
  items: AnomalySummary[] = [phaseA(), wafListItem(), ipsListItem()],
) {
  const list = vi.spyOn(api, "anomalies").mockResolvedValue(page(items));
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
  const view = render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
  return { ...view, list };
}
const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;

describe("an IPS row among Phase A and WAF rows", () => {
  it("names the entity, the IPS source and the IPS detector, with no EPS or volume wording", async () => {
    await renderPage();
    const row = rowOf("Destination port 445/tcp");
    expect(within(row).getByText("lab-ips-01")).toBeInTheDocument();
    expect(within(row).getByText("Destination-port rate spike")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("IPS_PORT_RATE_SPIKE");
    expect(row).not.toHaveTextContent("VOLUME_SPIKE");
    expect(row.textContent).not.toMatch(/EPS|volume|req/i);
  });

  it("states the unit in the cell: events per bucket, with the deviation ratio unitless", async () => {
    await renderPage();
    const row = rowOf("Destination port 445/tcp");
    expect(row).toHaveTextContent("940");
    expect(row).toHaveTextContent("940 → 120");
    expect(row).toHaveTextContent("events / bucket");
    expect(row).toHaveTextContent("7.83");
  });

  it("puts the unit on its own wrappable line under the figures, so it is never cut", async () => {
    await renderPage();
    const cell = rowOf("Destination port 445/tcp").querySelector("td.num")!;
    expect(cell.querySelector(".ips-figures")).toHaveTextContent("940 → 120");
    expect(cell.querySelector(".ips-unit")).toHaveTextContent("events / bucket");
  });

  it("names a source-level row Source-level and shows a share in percent", async () => {
    await renderPage({}, [
      ipsListItem({
        subject_kind: "IPS_SOURCE",
        ips_entity: null,
        anomaly_type: "IPS_SOURCE_CONCENTRATION_SPIKE",
        observed_value: 0.62,
        expected_value: 0.18,
      }),
    ]);
    const row = rowOf("Source-level");
    expect(row).toHaveTextContent("62.0 %");
    expect(row).toHaveTextContent("18.0 %");
    expect(row.textContent).not.toMatch(/EPS|events/);
  });

  it("shows fan-out in ports", async () => {
    await renderPage({}, [
      ipsListItem({
        subject_kind: "IPS_SOURCE",
        ips_entity: null,
        anomaly_type: "IPS_PORT_FANOUT_SPIKE",
        observed_value: 87,
        expected_value: 9,
      }),
    ]);
    expect(rowOf("Source-level")).toHaveTextContent("87");
    expect(rowOf("Source-level")).toHaveTextContent("distinct ports");
  });

  it("shows the new-port detector as expecting none", async () => {
    await renderPage({}, [
      ipsListItem({ anomaly_type: "IPS_NEW_DESTINATION_PORT", observed_value: 310, expected_value: null }),
    ]);
    const row = rowOf("Destination port 445/tcp");
    expect(row).toHaveTextContent("310 → none");
    expect(row).toHaveTextContent("events since first appeared");
    expect(row.textContent).not.toMatch(/bucket/);
  });

  it("writes a null observed value and a null ratio as dashes, never 0", async () => {
    await renderPage({}, [
      ipsListItem({ observed_value: null, expected_value: null, deviation_ratio: null, observed_measurement: null }),
    ]);
    const row = rowOf("Destination port 445/tcp");
    expect(row).toHaveTextContent("— → —");
    expect(row).not.toHaveTextContent(/\b0\b/);
  });

  it("reads a lower bound as at least n", async () => {
    await renderPage({}, [ipsListItem({ observed_measurement: "LOWER_BOUND" })]);
    expect(rowOf("Destination port 445/tcp")).toHaveTextContent("≥ 940");
  });

  it("shows an unknown IPS type as sent, with no figure under a guessed unit", async () => {
    await renderPage({}, [ipsListItem({ anomaly_type: "IPS_FUTURE" })]);
    const row = rowOf("Destination port 445/tcp");
    expect(within(row).getByText("IPS_FUTURE")).toBeInTheDocument();
    expect(row).not.toHaveTextContent("940");
  });

  it("drops the EPS unit from the heading while an IPS row is on the page", async () => {
    await renderPage();
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(heads[3]).toBe("Observed → Expected");
  });

  it("says evidence collection does not apply, rather than offering to request it", async () => {
    await renderPage({}, [ipsListItem()]);
    expect(screen.getAllByText(/Evidence collection does not apply to IPS anomalies/).length).toBeGreaterThan(0);
  });
});

describe("the IPS filters", () => {
  it("offers IPS entities and IPS sources as subjects", async () => {
    await renderPage();
    const select = screen.getByLabelText("Subject kind") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "",
      "LOG_SOURCE",
      "WAF_ENDPOINT",
      "IPS_ENTITY",
      "IPS_SOURCE",
      "IPS",
      "PW_VALUE",
    ]);
  });

  it("offers the seven IPS types in their own group", async () => {
    await renderPage();
    const group = screen.getByRole("group", { name: "IPS entities and sources" });
    expect(within(group).getAllByRole("option")).toHaveLength(7);
    expect(within(group).getByRole("option", { name: "Port fan-out spike" })).toBeInTheDocument();
  });

  it("sends an IPS subject kind and an IPS detector type", async () => {
    const { list } = await renderPage({
      subject_kind: "IPS_SOURCE",
      anomaly_type: "IPS_BLOCK_RATIO_SHIFT",
    });
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({ subject_kind: "IPS_SOURCE", anomaly_type: "IPS_BLOCK_RATIO_SHIFT" }),
    );
    expect(screen.getByRole("link", { name: /Detector: Block-ratio shift/ })).toBeInTheDocument();
  });

  it("sends both repeated API kinds for the combined IPS URL and keeps it in pagination", async () => {
    vi.spyOn(api, "anomalies").mockResolvedValue({
      items: [ipsListItem()],
      total: 60,
      limit: 25,
      offset: 0,
    });
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    render(
      await AnomaliesPage({
        searchParams: Promise.resolve({ subject_kind: "IPS", active_only: "true" }),
      }),
    );
    expect(api.anomalies).toHaveBeenCalledWith(
      expect.objectContaining({
        subject_kind: ["IPS_ENTITY", "IPS_SOURCE"],
        active_only: true,
      }),
    );
    expect(screen.getByRole("link", { name: /IPS entities and sources/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute(
      "href",
      expect.stringContaining("subject_kind=IPS"),
    );
  });

  it("passes ips_entity_id through, shows a chip, and keeps it across a re-filter and pagination", async () => {
    const id = "e1500000-0000-4000-8000-00000000000b";
    vi.spyOn(api, "anomalies").mockResolvedValue({ items: [ipsListItem()], total: 60, limit: 25, offset: 0 });
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    const { container } = render(
      await AnomaliesPage({ searchParams: Promise.resolve({ ips_entity_id: id }) }),
    );
    expect(api.anomalies).toHaveBeenCalledWith(expect.objectContaining({ ips_entity_id: id }));
    expect(screen.getByRole("link", { name: /IPS entity: e1500000/ })).toBeInTheDocument();
    expect(container.querySelector(`input[type=hidden][name=ips_entity_id][value="${id}"]`)).not.toBeNull();
    expect(screen.getByRole("link", { name: "Next" }).getAttribute("href")).toContain(`ips_entity_id=${id}`);
  });

  it("does not send an ips_entity_id that is not a UUID", async () => {
    const { list } = await renderPage({ ips_entity_id: "x' OR 1=1" });
    expect(list.mock.calls[0][0]?.ips_entity_id).toBeUndefined();
  });
});
