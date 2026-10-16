import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NewSearchPage from "./page";
import { SEARCH_LATENCY_STATEMENT } from "@/components/searches/authoring";
import { api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

const writer = {
  subject: "ada",
  email: null,
  permissions: ["search:write"],
  effective_permissions: ["read:searches", "search:write"],
};

const reader = {
  subject: "analyst",
  email: null,
  permissions: ["read:*", "search:execute"],
  effective_permissions: ["read:searches", "search:execute"],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("new search page", () => {
  it("shows the editor and the latency statement to a writer", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    render(await NewSearchPage());
    expect(screen.getByRole("heading", { name: "New search", level: 1 })).toBeInTheDocument();
    expect(
      screen.getByText(
        "A new search is saved disabled. Run it from its page to preview the result; only the saved query ever runs.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Saved as a disabled version/)).toBeNull();
    expect(screen.getByRole("textbox", { name: "AQL" })).toBeInTheDocument();
    expect(screen.getAllByText(SEARCH_LATENCY_STATEMENT)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Save as disabled" })).toBeInTheDocument();
  });

  it("hides the editor from a reader and names who can grant search:write", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    const { container } = render(await NewSearchPage());
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.queryByText(SEARCH_LATENCY_STATEMENT)).toBeNull();
    expect(screen.getByText(/The editor is hidden/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("search:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
    expect(screen.getByRole("link", { name: "Back to searches" })).toHaveAttribute("href", "/searches");
  });

  it("hides the editor when permissions cannot be read", async () => {
    vi.spyOn(api, "caller").mockRejectedValue(new Error("down"));
    const { container } = render(await NewSearchPage());
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.getByText(/could not be read/, { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("detection_engineer")).toBeInTheDocument();
  });
});
