import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RulesPage from "./page";
import { api } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "rules").mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  vi.spyOn(api, "ruleHealthSummary").mockResolvedValue([]);
});

describe("rules page header", () => {
  it("links to rule statistics from the page header", async () => {
    render(await RulesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Rule statistics" })).toHaveAttribute(
      "href",
      "/rules/statistics",
    );
  });
});
