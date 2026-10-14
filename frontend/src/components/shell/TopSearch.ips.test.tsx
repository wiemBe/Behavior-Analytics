import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopSearch } from "./TopSearch";
import { ipsListItem } from "@/components/ips/fixtures";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
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

beforeEach(() => {
  vi.useFakeTimers();
  api.searchSources.mockImplementation(() => page([]));
  api.searchRules.mockImplementation(() => page([]));
  api.searchOffenses.mockImplementation(() => page([]));
  api.activeAnomalies.mockImplementation(() =>
    page([ipsListItem({ id: "i1" }), ipsListItem({ id: "i2", subject_kind: "IPS_SOURCE", ips_entity: null })]),
  );
});
afterEach(() => vi.useRealTimers());

async function type(value: string) {
  fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
}

describe("top-bar search with IPS anomalies", () => {
  it("finds an IPS row by its entity and shows the IPS label, with no Phase A type or EPS wording", async () => {
    render(<TopSearch />);
    await type("445");
    const group = screen.getByRole("group", { name: "Active anomalies (first 200)" });
    const option = within(group).getByRole("option");
    expect(option).toHaveAttribute("href", "/anomalies/i1");
    expect(option).toHaveTextContent("Destination port 445/tcp");
    expect(option).toHaveTextContent("Destination-port rate spike · lab-ips-01 · OPEN");
    expect(option.textContent).not.toMatch(/IPS_PORT_RATE_SPIKE|EPS|volume/i);
  });

  it("finds a source-level row by its source name", async () => {
    render(<TopSearch />);
    await type("lab-ips");
    const options = within(screen.getByRole("group", { name: "Active anomalies (first 200)" })).getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[1]).toHaveTextContent("Source-level");
  });
});
