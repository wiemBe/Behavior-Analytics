import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Loading from "./loading";

describe("new search loading state", () => {
  it("announces the search editor and draws no zero", () => {
    render(<Loading />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading the search editor…");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("heading", { level: 1, name: "New search" })).toBeInTheDocument();
    expect(status).not.toHaveTextContent(/\b0\b/);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
