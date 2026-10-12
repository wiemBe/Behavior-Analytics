import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorState, LoadingState } from "./RouteState";

describe("LoadingState", () => {
  it("says what is loading, as a status", () => {
    render(<LoadingState what="anomalies" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading anomalies");
  });
});

describe("ErrorState", () => {
  it("says what failed and what the analyst can do", () => {
    render(<ErrorState what="Anomalies" onRetry={() => {}} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Anomalies could not be displayed");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("retries on request", async () => {
    const onRetry = vi.fn();
    render(<ErrorState what="Anomalies" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("quotes the opaque digest and never an exception message", () => {
    render(<ErrorState what="Anomalies" digest="51655969" onRetry={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Reference: 51655969");
  });

  it("omits the reference when there is no digest", () => {
    render(<ErrorState what="Anomalies" onRetry={() => {}} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Reference");
  });
});
