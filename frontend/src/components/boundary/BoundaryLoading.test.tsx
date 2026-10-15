import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BoundaryError, BoundaryLoading } from "./BoundaryLoading";

describe("BoundaryLoading", () => {
  it("renders the route heading before the shared plain-text loading state", () => {
    render(<BoundaryLoading title="Anomalies" what="anomalies" />);
    expect(screen.getByRole("heading", { level: 1, name: "Anomalies" })).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading anomalies…");
    expect(status).toHaveClass("notice");
  });

  it("renders no skeleton, spinner, data, or inline style", () => {
    const { container } = render(<BoundaryLoading title="Route" what="the route" />);
    expect(container.querySelector(".boundary-bar, [aria-busy='true']")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(container.querySelector("[style]")).toBeNull();
  });
});

describe("BoundaryError", () => {
  it("renders the route heading and delegates retry to the shared error state", async () => {
    const reset = vi.fn();
    render(
      <BoundaryError
        title="Anomalies"
        what="Anomalies"
        digest="ref-58"
        onRetry={reset}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Anomalies" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: ref-58.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
