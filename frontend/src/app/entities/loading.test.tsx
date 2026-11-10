import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Loading from "./loading";

describe("entities route loading state", () => {
  it("keeps the heading and announces what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1, name: "Entity timeline" })).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading the entity timeline…");
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("is plain text, not data", () => {
    render(<Loading />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).not.toHaveTextContent(/\b0\b/);
  });
});
