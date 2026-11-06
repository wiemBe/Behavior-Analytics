import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyLink } from "./CopyLink";

function clipboard(writeText: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => vi.useRealTimers());

describe("CopyLink", () => {
  it("copies the absolute address of the view and confirms once, then goes quiet", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    clipboard(writeText);
    render(<CopyLink href="/alerts?range=24h&severity=HIGH" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy a link to this view" }));
    expect(await screen.findByText("Link copied")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/alerts?range=24h&severity=HIGH`);
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.queryByText("Link copied")).toBeNull();
  });

  it("says what to do when the browser refuses, instead of claiming success", async () => {
    clipboard(vi.fn().mockRejectedValue(new Error("denied")));
    render(<CopyLink href="/alerts" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy a link to this view" }));
    expect(await screen.findByText(/did not allow copying/)).toBeInTheDocument();
    expect(screen.queryByText("Link copied")).toBeNull();
  });
});
