import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { chipLabel } from "@/components/behavior/AnomalyFilters";
import { HOSTILE, incident, WATCH_ID } from "@/components/propertywatch/fixtures";
import { api, type AnomalySummary, type Page } from "@/lib/api";

function page(items: AnomalySummary[]): Page<AnomalySummary> {
  return { items, total: items.length, limit: 25, offset: 0 };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  params: Record<string, string | undefined> = {},
  items: AnomalySummary[] = [incident()],
) {
  const list = vi.spyOn(api, "anomalies").mockResolvedValue(page(items));
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
  const view = render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
  return { ...view, list };
}

const row = () => screen.getByText("svc-backup").closest("tr")!;

describe("a property-watch row", () => {
  it("names the value, the watch that tracks it, the detector as a word and the state", async () => {
    await renderPage();
    expect(row()).toHaveTextContent("svc-backup");
    expect(within(row()).getByRole("link", { name: "VPN usernames" })).toHaveAttribute(
      "href",
      `/behavior/watches/${WATCH_ID}`,
    );
    expect(within(row()).getByText("Value spike")).toBeInTheDocument();
    expect(row()).not.toHaveTextContent("PW_VALUE_SPIKE");
    expect(within(row()).getByText("OPEN")).toBeInTheDocument();
  });

  it("states the unit in the cell and drops the EPS unit from the heading", async () => {
    await renderPage();
    expect(row()).toHaveTextContent("420");
    expect(row()).toHaveTextContent("100");
    expect(row()).toHaveTextContent("events per 5 min");
    expect(screen.getAllByRole("columnheader")[3]).toHaveTextContent("Observed → Expected");
    expect(screen.getAllByRole("columnheader")[3]).not.toHaveTextContent("EPS");
  });

  it("reads a lower-bound observed count as 'at least'", async () => {
    await renderPage({}, [incident({ observed_measurement: "LOWER_BOUND", observed_value: 412 })]);
    expect(row()).toHaveTextContent("≥ 412");
  });

  it("gives a new-value row no figure rather than a number under a guessed unit", async () => {
    await renderPage({}, [incident({ anomaly_type: "PW_NEW_VALUE", observed_value: 31, expected_value: null })]);
    expect(row()).toHaveTextContent("New value");
    expect(row().cells[3]).toHaveTextContent("—");
    expect(row().cells[3]).not.toHaveTextContent("31");
  });

  it("never turns a missing observed value into zero", async () => {
    await renderPage({}, [incident({ observed_value: null, expected_value: null, observed_measurement: null })]);
    expect(row()).toHaveTextContent("— → —");
  });

  it("renders a hostile value as text", async () => {
    const hostile = incident({
      pw_value: { ...incident().pw_value!, value_key: HOSTILE, components: [HOSTILE] },
    });
    const { container } = await renderPage({}, [hostile]);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
  });

  it("does not offer evidence collection for it", async () => {
    await renderPage();
    expect(screen.getAllByText(/Evidence collection does not apply to property watch anomalies/).length).toBeGreaterThan(0);
  });

  it("opens the investigation by the anomaly id", async () => {
    await renderPage();
    expect(within(row()).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/aaaaaaaa-0000-4000-8000-000000000001",
    );
  });
});

describe("the property-watch filters", () => {
  it("offers the kind and its five detectors in their own group", async () => {
    await renderPage();
    const group = screen.getByRole("group", { name: "Property watch values" });
    expect(within(group).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Value spike", "Value drop", "Value disappeared", "New value", "New combination",
    ]);
  });

  it("sends the kind, the detector and the watch to the API and shows chips", async () => {
    const { list } = await renderPage({
      subject_kind: "PW_VALUE",
      anomaly_type: "PW_NEW_VALUE",
      property_watch_id: WATCH_ID,
    });
    expect(list).toHaveBeenCalledWith(
      expect.objectContaining({
        subject_kind: "PW_VALUE",
        anomaly_type: "PW_NEW_VALUE",
        property_watch_id: WATCH_ID,
      }),
    );
    expect(screen.getByRole("link", { name: /Subject: Property watch values/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Detector: New value/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Remove filter Property watch: 6f1c0f9e/ })).toHaveAttribute(
      "href",
      expect.not.stringContaining("property_watch_id"),
    );
    expect(chipLabel("property_watch_id", WATCH_ID, [])).toBe("Property watch: 6f1c0f9e…");
  });

  it("drops a watch id that is not a uuid, and a detector it does not know", async () => {
    const { list } = await renderPage({ property_watch_id: "not-a-uuid", anomaly_type: "PW_NOPE" });
    const arg = list.mock.calls[0][0]!;
    expect(arg.property_watch_id).toBeUndefined();
    expect(arg.anomaly_type).toBeUndefined();
  });

  it("carries the watch through a re-filter so the link does not silently widen", async () => {
    const { container } = await renderPage({ property_watch_id: WATCH_ID });
    expect(container.querySelector('input[type="hidden"][name="property_watch_id"]')).toHaveAttribute("value", WATCH_ID);
  });
});
