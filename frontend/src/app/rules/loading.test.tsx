import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Loading from "./loading";

describe("rules route loading state", () => {
  it("announces what is loading to assistive technology", () => {
    render(<Loading />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading rules…");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("heading", { level: 1, name: "Rule health" })).toBeInTheDocument();
  });

  it("is plain text, not a skeleton or data", () => {
    const { container } = render(<Loading />);
    expect(container.querySelector(".boundary-bar, [aria-busy='true']")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/no results/i)).toBeNull();
    expect(screen.getByRole("status")).not.toHaveTextContent(/\b0\b/);
  });
});
