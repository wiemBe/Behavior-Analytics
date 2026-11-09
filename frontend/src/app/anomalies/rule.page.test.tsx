import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnomaliesPage from "./page";
import { ruleListItem, RULE_ID } from "@/components/rules/ruleMatchFixtures";
import { api, type AnomalySummary, type Page } from "@/lib/api";

function page(items: AnomalySummary[]): Page<AnomalySummary> {
  return { items, total: items.length, limit: 25, offset: 0 };
}

beforeEach(() => vi.restoreAllMocks());

async function renderPage(
  params: Record<string, string | undefined> = {},
  items: AnomalySummary[] = [ruleListItem()],
) {
  const list = vi.spyOn(api, "anomalies").mockResolvedValue(page(items));
  vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
  const view = render(await AnomaliesPage({ searchParams: Promise.resolve(params) }));
  return { ...view, list };
}

const rowOf = () => screen.getAllByRole("row")[1];

describe("the Rule family on /anomalies", () => {
  it("offers Rule as a subject kind", async () => {
    await renderPage();
    const select = screen.getByLabelText("Subject kind") as HTMLSelectElement;
    const option = [...select.options].find((o) => o.value === "RULE");
    expect(option?.textContent).toBe("Rule");
  });

  it("sends subject_kind RULE to the API and shows it as a removable chip", async () => {
    const { list } = await renderPage({ subject_kind: "RULE" });
    expect(list.mock.calls[0][0]).toMatchObject({ subject_kind: "RULE" });
    const chip = screen.getByRole("link", { name: "Remove filter Subject: Rule" });
    expect(chip).toHaveAttribute("href", "/anomalies");
  });

  it("offers the two rule detectors in their own group and sends the chosen type", async () => {
    const { list } = await renderPage({ anomaly_type: "RULE_STORM" });
    const group = screen.getByRole("group", { name: "Rule match" });
    expect(within(group).getAllByRole("option").map((o) => o.textContent)).toEqual(["Rule silence", "Rule storm"]);
    expect(list.mock.calls[0][0]).toMatchObject({ anomaly_type: "RULE_STORM" });
  });

  it("renders the subject as the rule name and QRadar id, linked to the rule", async () => {
    await renderPage();
    const r = rowOf();
    const link = within(r).getByRole("link", { name: "Multiple login failures from a single source" });
    expect(link).toHaveAttribute("href", `/rules/${RULE_ID}`);
    expect(r).toHaveTextContent("QRadar id 100123");
    expect(r).not.toHaveTextContent("Building block");
    expect(r).not.toHaveTextContent("Unnamed subject");
  });

  it("marks a truncated window's observed count as a lower bound and says so on the row", async () => {
    await renderPage({}, [ruleListItem({ observed_value: 10000, observed_measurement: "LOWER_BOUND" })]);
    const r = rowOf();
    expect(r).toHaveTextContent("≥ 10,000");
    expect(r).toHaveTextContent("Truncated window: at least this many matches.");
  });

  it("shows an exact count without the bound or the note", async () => {
    await renderPage({}, [ruleListItem({ observed_value: 4800, observed_measurement: "EXACT" })]);
    const r = rowOf();
    expect(r).toHaveTextContent("4,800");
    expect(r).not.toHaveTextContent("≥");
    expect(r).not.toHaveTextContent("Truncated window");
  });

  it("never draws a lower-bound mark on a missing count", async () => {
    await renderPage({}, [ruleListItem({ observed_value: null, observed_measurement: "LOWER_BOUND" })]);
    expect(rowOf()).not.toHaveTextContent("≥");
  });

  it("adds the Building block marker when the flag is set", async () => {
    await renderPage({}, [
      ruleListItem({
        rule: { rule_id: RULE_ID, name: "BB: Remote access", qradar_id: 7, is_building_block: true },
      }),
    ]);
    expect(rowOf()).toHaveTextContent("QRadar id 7 · Building block");
  });

  it("names the detector as a word and gives observed and expected their unit", async () => {
    await renderPage();
    const r = rowOf();
    expect(r).toHaveTextContent("Rule storm");
    expect(r).not.toHaveTextContent("RULE_STORM");
    expect(r).toHaveTextContent("4,800");
    expect(r).toHaveTextContent("310.5");
    expect(r).toHaveTextContent("matches / window");
    expect(r).not.toHaveTextContent("EPS");
    expect(screen.getAllByRole("columnheader")[3]).toHaveTextContent("Observed → Expected");
    expect(screen.getAllByRole("columnheader")[3]).not.toHaveTextContent("EPS");
  });

  it("never turns a missing count into zero", async () => {
    await renderPage({}, [ruleListItem({ observed_value: null, expected_value: null, deviation_ratio: null })]);
    expect(rowOf()).toHaveTextContent("— → —");
  });

  it("keeps a silence's measured zero", async () => {
    await renderPage({}, [
      ruleListItem({ anomaly_type: "RULE_SILENCE", observed_value: 0, expected_value: 12, deviation_ratio: null }),
    ]);
    expect(rowOf()).toHaveTextContent("Rule silence");
    expect(rowOf()).toHaveTextContent("0 → 12");
  });

  it("says evidence collection does not apply, as it does for the other families", async () => {
    await renderPage();
    expect(rowOf()).toHaveTextContent("Evidence collection does not apply to rule-match anomalies");
  });

  it("links the row to its investigation", async () => {
    await renderPage();
    expect(within(rowOf()).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/99999999-0000-4000-8000-000000000001",
    );
  });
});
