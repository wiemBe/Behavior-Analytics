import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { HelpTip } from "./HelpTip";

function setup(onShownChange = vi.fn()) {
  render(
    <>
      <HelpTip id="tip" name="About Metric" text="What is read per slice." onShownChange={onShownChange} />
      <button type="button">elsewhere</button>
    </>,
  );
  return { button: screen.getByRole("button", { name: "About Metric" }), onShownChange };
}

describe("HelpTip", () => {
  it("is a real button with an accessible name, closed at first", () => {
    const { button } = setup();
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens on keyboard focus and closes on blur", async () => {
    const user = userEvent.setup();
    const { button, onShownChange } = setup();
    await user.tab();
    expect(button).toHaveFocus();
    expect(screen.getByRole("tooltip")).toHaveTextContent("What is read per slice.");
    expect(onShownChange).toHaveBeenLastCalledWith(true);
    await user.tab();
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(onShownChange).toHaveBeenLastCalledWith(false);
  });

  it("opens on hover and closes when the pointer leaves", async () => {
    const user = userEvent.setup();
    const { button } = setup();
    await user.hover(button);
    expect(screen.getByRole("tooltip")).toBeVisible();
    await user.unhover(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens on click and a second click closes it", async () => {
    const user = userEvent.setup();
    const { button } = setup();
    await user.click(button);
    expect(screen.getByRole("tooltip")).toBeVisible();
    await user.click(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("closes on Escape while the button keeps focus", async () => {
    const user = userEvent.setup();
    const { button } = setup();
    await user.tab();
    expect(screen.getByRole("tooltip")).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("stays open after a click until blur", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "About Metric" }));
    await user.unhover(screen.getByRole("button", { name: "About Metric" }));
    expect(screen.getByRole("tooltip")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
