import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import EntitiesError from "./error";

describe("entities route error boundary", () => {
  it("says what could not be displayed, what to do and the digest, without the exception", () => {
    render(
      <EntitiesError error={Object.assign(new Error("secret stack"), { digest: "ref-16" })} reset={() => {}} />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Entity timeline" })).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The entity timeline could not be displayed.");
    expect(alert).toHaveTextContent("Nothing on it was changed.");
    expect(alert).toHaveTextContent("Reference: ref-16.");
    expect(alert).not.toHaveTextContent("secret stack");
  });

  it("retries through the reset prop", async () => {
    const reset = vi.fn();
    render(<EntitiesError error={new Error("x")} reset={reset} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
