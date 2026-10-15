import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AlertActions } from "./AlertActions";
import { ApiError, api } from "@/lib/api";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

beforeEach(() => {
  refresh.mockClear();
});

describe("AlertActions", () => {
  it("acknowledges and refreshes the server component", async () => {
    const ack = vi.spyOn(api, "acknowledgeAlert").mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(<AlertActions alertId="a-1" status="OPEN" />);

    await user.click(screen.getByRole("button", { name: "Acknowledge" }));

    await waitFor(() => expect(ack).toHaveBeenCalledWith("a-1"));
    expect(await screen.findByRole("status")).toHaveTextContent("Alert acknowledged.");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("sends the typed resolution reason", async () => {
    const resolve = vi.spyOn(api, "resolveAlert").mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(<AlertActions alertId="a-1" status="ACKNOWLEDGED" />);

    await user.type(screen.getByLabelText("Resolution reason"), "  log source restored  ");
    await user.click(screen.getByRole("button", { name: "Resolve" }));
    await user.click(screen.getByRole("button", { name: "Resolve alert" }));

    await waitFor(() =>
      expect(resolve).toHaveBeenCalledWith("a-1", "log source restored"),
    );
  });

  it("disables controls while a request is in flight", async () => {
    let release: (v: unknown) => void = () => {};
    vi.spyOn(api, "acknowledgeAlert").mockReturnValue(
      new Promise((res) => {
        release = res;
      }) as never,
    );
    const user = userEvent.setup();
    render(<AlertActions alertId="a-1" status="OPEN" />);

    const button = screen.getByRole("button", { name: "Acknowledge" });
    await user.click(button);

    expect(await screen.findByRole("button", { name: "Acknowledging…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Resolve" })).toBeDisabled();
    expect(screen.getByLabelText("Resolution reason")).toBeDisabled();

    // Settle the request so the component finishes its state updates inside act.
    await act(async () => {
      release({});
    });
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeEnabled();
  });

  it("does not submit twice when clicked repeatedly", async () => {
    // The request stays in flight until this test releases it, so the repeated
    // clicks always land while the guard is up, whatever the machine load is.
    let release: (v: unknown) => void = () => {};
    const ack = vi
      .spyOn(api, "acknowledgeAlert")
      .mockReturnValue(
        new Promise((res) => {
          release = res;
        }) as never,
      );
    const user = userEvent.setup();
    render(<AlertActions alertId="a-1" status="OPEN" />);

    const button = screen.getByRole("button", { name: "Acknowledge" });
    await user.click(button);
    await user.click(button);
    await user.click(button);

    expect(await screen.findByRole("button", { name: "Acknowledging…" })).toBeDisabled();

    // Settle the request so the component finishes its state updates inside act.
    await act(async () => {
      release({});
    });

    await waitFor(() => expect(screen.queryByRole("status")).not.toBeNull());
    // A second acknowledge would write another audit row and enqueue another page.
    expect(ack).toHaveBeenCalledTimes(1);
  });

  it("surfaces a 403 as a permissions message, not a generic failure", async () => {
    vi.spyOn(api, "acknowledgeAlert").mockRejectedValue(new ApiError(403, "alert:ack required"));
    const user = userEvent.setup();
    render(<AlertActions alertId="a-1" status="OPEN" />);

    await user.click(screen.getByRole("button", { name: "Acknowledge" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You do not have permission to perform this action.",
    );
    expect(refresh).not.toHaveBeenCalled();
  });

  it("never renders a raw exception message", async () => {
    vi.spyOn(api, "acknowledgeAlert").mockRejectedValue(
      new Error("connect ECONNREFUSED 10.0.0.5:8000 token=abc123"),
    );
    const user = userEvent.setup();
    render(<AlertActions alertId="a-1" status="OPEN" />);

    await user.click(screen.getByRole("button", { name: "Acknowledge" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The request could not be sent. Check your connection and try again.",
    );
    expect(alert.textContent).not.toContain("token=abc123");
    expect(alert.textContent).not.toContain("10.0.0.5");
  });

  it("offers no acknowledge control once acknowledged", () => {
    render(<AlertActions alertId="a-1" status="ACKNOWLEDGED" />);
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
    expect(screen.getByRole("button", { name: "Resolve" })).toBeEnabled();
  });

  it("offers no actions once resolved", () => {
    render(<AlertActions alertId="a-1" status="RESOLVED" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/no further action is available/i)).toBeInTheDocument();
  });

  describe("confirming a resolve", () => {
    it("asks first and calls nothing until confirmed", async () => {
      const resolve = vi.spyOn(api, "resolveAlert").mockResolvedValue({} as never);
      const user = userEvent.setup();
      render(<AlertActions alertId="a-1" status="OPEN" />);

      expect(screen.queryByText(/cannot be reopened/)).toBeNull();
      await user.click(screen.getByRole("button", { name: "Resolve" }));

      expect(screen.getByText("Resolve this alert? A resolved alert cannot be reopened.")).toBeVisible();
      expect(resolve).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "Resolve alert" }));
      await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(screen.queryByText(/cannot be reopened/)).toBeNull());
    });

    it("moves focus into the confirmation and back to Resolve on Cancel", async () => {
      const resolve = vi.spyOn(api, "resolveAlert").mockResolvedValue({} as never);
      const user = userEvent.setup();
      render(<AlertActions alertId="a-1" status="OPEN" />);

      await user.click(screen.getByRole("button", { name: "Resolve" }));
      expect(screen.getByRole("button", { name: "Resolve alert" })).toHaveFocus();

      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByText(/cannot be reopened/)).toBeNull();
      expect(screen.getByRole("button", { name: "Resolve" })).toHaveFocus();
      expect(resolve).not.toHaveBeenCalled();
    });

    it("leaves Acknowledge a single click", async () => {
      const ack = vi.spyOn(api, "acknowledgeAlert").mockResolvedValue({} as never);
      const user = userEvent.setup();
      render(<AlertActions alertId="a-1" status="OPEN" />);
      await user.click(screen.getByRole("button", { name: "Acknowledge" }));
      await waitFor(() => expect(ack).toHaveBeenCalledTimes(1));
      expect(screen.queryByText(/cannot be reopened/)).toBeNull();
    });
  });
});
