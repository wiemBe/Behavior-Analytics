import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TopSearch } from "./TopSearch";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const api = vi.hoisted(() => ({
  searchSources: vi.fn(),
  searchRules: vi.fn(),
  searchOffenses: vi.fn(),
  activeAnomalies: vi.fn(),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  shellApi: api,
}));

beforeEach(() => {
  vi.useFakeTimers();
  const empty = { items: [], total: 0, limit: 5, offset: 0 };
  Object.values(api).forEach((read) => read.mockResolvedValue(empty));
});
afterEach(() => vi.useRealTimers());

it("references the results list only while that list exists", async () => {
  render(<TopSearch />);
  const search = screen.getByRole("combobox", {
    name: "Search sources, rules, offenses and anomalies",
  });
  expect(search).not.toHaveAttribute("aria-controls");

  fireEvent.change(search, { target: { value: "fi" } });
  await act(async () => vi.advanceTimersByTimeAsync(250));
  const list = screen.getByRole("listbox", { name: "Search results" });
  expect(search).toHaveAttribute("aria-controls", list.id);

  fireEvent.keyDown(search, { key: "Escape" });
  expect(screen.queryByRole("listbox", { name: "Search results" })).toBeNull();
  expect(search).not.toHaveAttribute("aria-controls");
});
