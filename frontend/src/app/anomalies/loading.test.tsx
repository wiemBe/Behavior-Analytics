import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Loading from "./loading";

describe("anomalies route loading state", () => {
  it("announces what is loading to assistive technology", () => {
    render(<Loading />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading anomalies…");
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("shows no data, no zero and no 'No results' text while it waits", () => {
    render(<Loading />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText(/no results/i)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("status")).not.toHaveTextContent(/\b0\b/);
  });
});
