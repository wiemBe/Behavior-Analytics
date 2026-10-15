import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AlertsError from "./error";

describe("alerts route error boundary", () => {
  it("says what could not be displayed, what to do, and the digest, without the exception", () => {
    render(
      <AlertsError
        error={Object.assign(new Error("secret stack"), { digest: "ref-58" })}
        reset={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Alerts" })).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Alerts could not be displayed.");
    expect(alert).toHaveTextContent("Nothing on it was changed.");
    expect(alert).toHaveTextContent(/check that the backend is reachable/i);
    expect(alert).toHaveTextContent("Reference: ref-58.");
    expect(alert).not.toHaveTextContent("secret stack");
  });

  it("retries through the reset prop and hides a digest that is not there", async () => {
    const reset = vi.fn();
    render(<AlertsError error={new Error("x")} reset={reset} />);
    expect(screen.getByRole("alert")).not.toHaveTextContent("Reference:");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
