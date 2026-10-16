import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import DashboardError from "./error";

describe("dashboard error boundary", () => {
  it("is a styled alert that says what failed and what to do", () => {
    render(<DashboardError error={new Error("secret stack")} reset={() => {}} />);
    expect(screen.getByRole("heading", { level: 1, name: "SOC Overview" })).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveClass("notice");
    expect(alert).toHaveTextContent("Dashboard could not be opened.");
    expect(alert).toHaveTextContent("The page could not be displayed.");
    expect(alert).toHaveTextContent(/check that the backend is reachable/i);
    expect(alert).not.toHaveTextContent("secret stack");
  });

  it("offers a retry when Next hands it reset", async () => {
    const reset = vi.fn();
    render(<DashboardError error={new Error("x")} reset={reset} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
