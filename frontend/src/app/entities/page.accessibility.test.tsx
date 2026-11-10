import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import EntitiesPage from "./page";
import { api, type EntityTimeline } from "@/lib/api";

// Names and structure, as the other accessibility tests pin them: one h1, labelled form controls,
// named tables with column and row headers, links that say where they go, and a time element
// with a datetime for every instant.

const answer: EntityTimeline = {
  entity: { kind: "IP", value: "192.0.2.44", value_norm: "192.0.2.44" },
  window: { start: "2026-11-09T12:00:00Z", end: "2026-11-10T12:00:00Z" },
  state: "PARTIAL",
  reason_code: "entity_timeline.reason.partial",
  lanes: [
    { source: "EXPLANATION", state: "OK", reason_code: null, total: 1, permission: null },
    { source: "WAF_TOP", state: "PARTIAL", reason_code: "entity_timeline.reason.not_among_stored_top", total: null, permission: null },
    { source: "IPS_TOP", state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.family_off", total: null, permission: null },
    { source: "IPS_ENTITY", state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.family_off", total: null, permission: null },
    { source: "OFFENSE", state: "NOT_COLLECTED", reason_code: "entity_timeline.reason.permission", total: null, permission: "read:offenses" },
    { source: "LOG_SOURCE", state: "OK", reason_code: null, total: 0, permission: null },
  ],
  items: [
    {
      occurred_at: "2026-11-10T08:02:00Z",
      occurred_end: null,
      source: "EXPLANATION",
      role: "SOURCE_IP",
      title: "Volume spike on Edge firewall",
      title_code: null,
      title_params: null,
      href: "/anomalies/a1",
      severity: "HIGH",
      measurement: "TOP_N",
    },
  ],
  dangling: 0,
  truncated: false,
  next_cursor: null,
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "entityTimeline").mockResolvedValue(answer);
});

async function renderPage(params: Record<string, string>) {
  return render(await EntitiesPage({ searchParams: Promise.resolve(params) }));
}

it("has exactly one h1 and names the form, its controls and the kind group", async () => {
  await renderPage({ value: "192.0.2.44" });
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  const form = screen.getByRole("form", { name: "Entity search" });
  expect(within(form).getByRole("textbox", { name: /Value/ })).toBeInTheDocument();
  const group = within(form).getByRole("group", { name: "What it is" });
  expect(within(group).getAllByRole("radio")).toHaveLength(3);
  expect(within(form).getByRole("button", { name: "Show timeline" })).toBeInTheDocument();
});

it("names both tables and gives every column and every lane a header", async () => {
  await renderPage({ value: "192.0.2.44" });
  const lanes = screen.getByRole("table", { name: "Entity timeline lanes" });
  expect(within(lanes).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
    "Lane",
    "Status",
    "Stored mentions",
    "Note",
  ]);
  expect(within(lanes).getAllByRole("rowheader")).toHaveLength(6);
  const list = screen.getByRole("table", { name: "Stored mentions of the entity, newest first" });
  expect(within(list).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
    "Time (UTC)",
    "Lane",
    "Role",
    "Record",
    "Severity",
    "Basis",
  ]);
  // Each table scrolls in its own labelled, keyboard-reachable region, and no two regions share a name.
  const regions = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label") ?? r.getAttribute("aria-labelledby"));
  expect(new Set(regions).size).toBe(regions.length);
  expect(screen.getByRole("region", { name: "Entity timeline lanes" })).toHaveAttribute("tabindex", "0");
  expect(screen.getByRole("region", { name: "Stored mentions of the entity, newest first" })).toHaveAttribute("tabindex", "0");
});

it("gives every instant a time element with a datetime and the zone", async () => {
  const { container } = await renderPage({ value: "192.0.2.44" });
  const times = container.querySelectorAll("time");
  expect(times.length).toBeGreaterThanOrEqual(3);
  for (const t of times) {
    expect(t.getAttribute("datetime")).toMatch(/^2026-11-/);
    expect(`${t.getAttribute("title") ?? ""}${t.textContent}`).toMatch(/UTC/);
  }
});

it("labels the range control as a navigation and marks the current window", async () => {
  await renderPage({ value: "192.0.2.44", range: "7d" });
  const nav = screen.getByRole("navigation", { name: "Time range (UTC)" });
  expect(within(nav).getByRole("link", { name: "7 days" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "24 hours" })).not.toHaveAttribute("aria-current");
});

it("says the status in words, not by colour alone", async () => {
  await renderPage({ value: "192.0.2.44" });
  const lanes = screen.getByRole("table", { name: "Entity timeline lanes" });
  const words = within(lanes)
    .getAllByRole("row")
    .slice(1)
    .map((r) => within(r).getAllByRole("cell")[0].textContent);
  expect(words).toEqual(["Measured", "Partial", "Not collected", "Not collected", "Forbidden", "None stored"]);
});
