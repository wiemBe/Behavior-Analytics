// SOC-15b: `g` then a letter goes to a page; the sequence lives once, in the shell.

import { act, fireEvent, render } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GO_WINDOW_MS, GoKeys } from "./GoKeys";

const router = { push: vi.fn(), refresh: vi.fn() };
const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) =>
  act(() => {
    fireEvent.keyDown(target, { key, ...init });
  });

beforeEach(() => {
  vi.useFakeTimers();
  router.push.mockReset();
  render(
    <AppRouterContext.Provider value={router as never}>
      <GoKeys />
      <input aria-label="q" />
    </AppRouterContext.Provider>,
  );
});
afterEach(() => {
  vi.useRealTimers();
});

describe("g then a letter", () => {
  it.each([
    ["d", "/"],
    ["q", "/queue"],
    ["a", "/anomalies"],
    ["l", "/alerts"],
    ["o", "/offenses"],
  ])("g %s goes to %s", (key, route) => {
    press("g");
    press(key);
    expect(router.push).toHaveBeenCalledTimes(1);
    expect(router.push).toHaveBeenCalledWith(route);
  });

  it("goes nowhere on a letter alone", () => {
    press("d");
    press("q");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("times out after 1.5 s", () => {
    press("g");
    act(() => {
      vi.advanceTimersByTime(GO_WINDOW_MS + 1);
    });
    press("d");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("still goes just inside the window", () => {
    press("g");
    act(() => {
      vi.advanceTimersByTime(GO_WINDOW_MS - 10);
    });
    press("d");
    expect(router.push).toHaveBeenCalledWith("/");
  });

  it("is cancelled by any other key, which does not go either", () => {
    press("g");
    press("x");
    press("d");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("is one step: a second letter after a go does nothing", () => {
    press("g");
    press("d");
    press("q");
    expect(router.push).toHaveBeenCalledTimes(1);
  });

  it("takes the second key before any list handler sees it", () => {
    const seen = vi.fn();
    document.addEventListener("keydown", seen);
    press("g");
    seen.mockClear();
    press("a");
    expect(seen).not.toHaveBeenCalled();
    document.removeEventListener("keydown", seen);
  });

  it("is inert in an input, a select and with a modifier", () => {
    const input = document.querySelector("input")!;
    press("g", {}, input);
    press("d", {}, input);
    expect(router.push).not.toHaveBeenCalled();
    press("g", { ctrlKey: true });
    press("d");
    press("g", { metaKey: true });
    press("d");
    press("g", { altKey: true });
    press("d");
    expect(router.push).not.toHaveBeenCalled();
    // A modifier on the second key cancels too (Ctrl K stays the search).
    press("g");
    press("d", { ctrlKey: true });
    expect(router.push).not.toHaveBeenCalled();
  });

  it("is inert while a dialog is open", () => {
    const dialog = document.createElement("div");
    dialog.setAttribute("aria-modal", "true");
    document.body.appendChild(dialog);
    press("g");
    press("d");
    expect(router.push).not.toHaveBeenCalled();
    dialog.remove();
  });
});
