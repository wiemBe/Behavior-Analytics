// The Entities group of the top bar (SOC-16c): one row that opens the entity timeline for the typed
// text. The bar does not classify the text; the page and its API do. The row reads nothing, shows
// for any non-empty query, comes after every searched group and never moves their results.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopSearch } from "./TopSearch";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  usePathname: () => "/",
}));

const api = vi.hoisted(() => ({
  searchSources: vi.fn(),
  searchRules: vi.fn(),
  searchOffenses: vi.fn(),
  activeAnomalies: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  shellApi: api,
}));

const page = (items: unknown[]) => Promise.resolve({ items, total: items.length, limit: 5, offset: 0 });

function happy() {
  api.searchSources.mockImplementation(() => page([{ id: "s1", name: "Core Firewall A", type_name: "Cisco ASA" }]));
  api.searchRules.mockImplementation(() => page([{ id: "r1", qradar_id: 7001, name: "Excessive firewall denies" }]));
  api.searchOffenses.mockImplementation(() => page([{ qradar_offense_id: 4001, description: "Firewall scan", status: "OPEN" }]));
  api.activeAnomalies.mockImplementation(() =>
    page([{ id: "a1", log_source_id: "ls-a1", log_source_name: "Core Firewall A", anomaly_type: "VOLUME_SPIKE", state: "OPEN" }]),
  );
}

async function type(value: string, wait = 250) {
  fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(wait);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  push.mockReset();
  Object.values(api).forEach((f) => f.mockReset());
  happy();
});
afterEach(() => vi.useRealTimers());

describe("the Entities group", () => {
  it("is absent while nothing is typed", () => {
    render(<TopSearch />);
    fireEvent.focus(screen.getByRole("combobox"));
    expect(screen.queryByRole("group", { name: "Entities" })).toBeNull();
    expect(screen.queryByRole("option")).toBeNull();
  });

  it("offers one row for the typed text, before any search has answered, and reads nothing for it", async () => {
    render(<TopSearch />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "192.0.2.44" } });
    const group = screen.getByRole("group", { name: "Entities" });
    const rows = within(group).getAllByRole("option");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Entity timeline for “192.0.2.44”");
    expect(rows[0]).toHaveAttribute("href", "/entities?value=192.0.2.44");
    expect(api.searchSources).not.toHaveBeenCalled();
  });

  it("also shows for a single character, which the searched groups refuse", async () => {
    render(<TopSearch />);
    await type("a");
    expect(screen.getByRole("option")).toHaveAttribute("href", "/entities?value=a");
    expect(api.searchSources).not.toHaveBeenCalled();
  });

  it("trims the text and does not offer a row for blanks", async () => {
    render(<TopSearch />);
    await type("   ");
    expect(screen.queryByRole("group", { name: "Entities" })).toBeNull();
    await type("  host-1  ");
    expect(screen.getByRole("option", { name: /Entity timeline for/ })).toHaveAttribute("href", "/entities?value=host-1");
  });

  it.each([
    ["a/b", "/entities?value=a%2Fb"],
    ["a&b=c", "/entities?value=a%26b%3Dc"],
    ["a#b", "/entities?value=a%23b"],
    ["alice smith", "/entities?value=alice%20smith"],
    ["josé üğ", "/entities?value=jos%C3%A9%20%C3%BC%C4%9F"],
    ["50%", "/entities?value=50%25"],
  ])("encodes %j in the link", async (typed, href) => {
    render(<TopSearch />);
    await type(typed);
    const row = screen.getByRole("option", { name: /Entity timeline for/ });
    expect(row).toHaveAttribute("href", href);
    const parsed = new URL(href, "http://x");
    expect(parsed.searchParams.get("value")).toBe(typed);
    expect(parsed.searchParams.has("kind")).toBe(false);
  });

  it("comes last and leaves the other groups and their order as they were", async () => {
    render(<TopSearch />);
    await type("firewall");
    const labels = screen
      .getAllByRole("group")
      .map((g) => within(g).getByText(/Sources|Rules|Offenses|Active anomalies|Entities/, { selector: ".topsearch-group" }).textContent);
    expect(labels).toEqual(["Sources", "Rules", "Offenses", "Active anomalies (first 200)", "Entities"]);
    expect(screen.getAllByRole("option").map((o) => o.getAttribute("href"))).toEqual([
      "/behavior/sources/s1",
      "/rules/r1",
      "/offenses/4001",
      "/anomalies/a1",
      "/entities?value=firewall",
    ]);
  });

  it("stays when a searched group is refused or failed, beside its note", async () => {
    api.searchRules.mockRejectedValue(new Error("down"));
    render(<TopSearch />);
    await type("firewall");
    expect(screen.getByText("Rules could not be searched: the request failed. Try again.")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Entity timeline for/ })).toBeInTheDocument();
  });

  it("is reached by the arrow keys and followed with Enter", async () => {
    render(<TopSearch />);
    await type("firewall");
    const box = screen.getByRole("combobox");
    for (let i = 0; i < 5; i += 1) fireEvent.keyDown(box, { key: "ArrowDown" });
    const row = screen.getByRole("option", { name: /Entity timeline for/ });
    expect(row).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", row.id);
    fireEvent.keyDown(box, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/entities?value=firewall");
  });

  it("is the only row when nothing else matched, and ArrowUp from the top wraps onto it", async () => {
    api.searchSources.mockImplementation(() => page([]));
    api.searchRules.mockImplementation(() => page([]));
    api.searchOffenses.mockImplementation(() => page([]));
    api.activeAnomalies.mockImplementation(() => page([]));
    render(<TopSearch />);
    await type("web-01");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("No sources, rules, offenses or active anomalies match");
    const box = screen.getByRole("combobox");
    fireEvent.keyDown(box, { key: "ArrowUp" });
    expect(box).toHaveAttribute("aria-activedescendant", screen.getByRole("option").id);
  });

  it("follows a click and keeps a modified click for the browser", async () => {
    render(<TopSearch />);
    await type("web-01");
    const row = screen.getByRole("option", { name: /Entity timeline for/ });
    fireEvent.click(row, { ctrlKey: true });
    expect(push).not.toHaveBeenCalled();
    fireEvent.click(row);
    expect(push).toHaveBeenCalledWith("/entities?value=web-01");
  });
});
