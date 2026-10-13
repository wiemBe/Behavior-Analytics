import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RulesPage from "./page";
import Loading from "./loading";
import { ApiError, fleetApi, type ClassificationRule } from "@/lib/api";

function rule(over: Partial<ClassificationRule> = {}): ClassificationRule {
  return {
    id: "r1",
    instance_id: null,
    kind: "NAME_REGEX",
    pattern: "x",
    target_class: "IPS",
    priority: 100,
    enabled: true,
    built_in: true,
    description: null,
    created_by: null,
    created_at: "",
    updated_at: "",
    ...over,
  };
}

beforeEach(() => vi.restoreAllMocks());

describe("RulesPage", () => {
  it("states the precedence once, in order", async () => {
    vi.spyOn(fleetApi, "rules").mockResolvedValue([rule()]);
    render(await RulesPage());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const items = screen.getAllByRole("listitem").filter((li) => li.closest("ol"));
    expect(items.map((li) => li.querySelector("strong")?.textContent)).toEqual([
      "Operator override",
      "QRadar internal",
      "Group rules",
      "Type rules",
      "Name rules",
      "Unclassified",
    ]);
    expect(screen.getAllByText(/Order of decision/)).toHaveLength(1);
  });

  it("counts built-in and custom rules", async () => {
    vi.spyOn(fleetApi, "rules").mockResolvedValue([rule(), rule({ id: "r2", built_in: false, pattern: "y" })]);
    render(await RulesPage());
    expect(screen.getByText(/2 rules, 1 built-in and 1 custom/)).toBeInTheDocument();
  });

  it("says what failed and what to do", async () => {
    vi.spyOn(fleetApi, "rules").mockRejectedValue(new Error("down"));
    render(await RulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent(/reload/i);
    expect(screen.queryByText("Order of decision")).toBeNull();
  });

  it("names the permission on 403", async () => {
    vi.spyOn(fleetApi, "rules").mockRejectedValue(new ApiError(403, "no"));
    render(await RulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("read:fleet");
  });

  it("announces loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/classification rules/);
  });
});
