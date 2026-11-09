// SOC-15a: the refresh choice is remembered per browser; Off stays the default.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AutoRefresh, REFRESH_STORAGE_KEY } from "./AutoRefresh";

const refresh = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn() }) }));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-09T14:02:11Z"));
  refresh.mockReset();
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.body.innerHTML = "";
});

const select = () => screen.getByRole("combobox", { name: "Refresh" }) as HTMLSelectElement;

describe("the remembered refresh choice", () => {
  it("stays Off when nothing is stored", () => {
    render(<AutoRefresh />);
    expect(select().value).toBe("off");
  });

  it("stores the choice when it is changed", () => {
    render(<AutoRefresh />);
    fireEvent.change(select(), { target: { value: "5" } });
    expect(window.localStorage.getItem(REFRESH_STORAGE_KEY)).toBe("5");
    fireEvent.change(select(), { target: { value: "off" } });
    expect(window.localStorage.getItem(REFRESH_STORAGE_KEY)).toBe("off");
  });

  it("restores the stored choice and refreshes on its cadence", async () => {
    window.localStorage.setItem(REFRESH_STORAGE_KEY, "1");
    render(<AutoRefresh />);
    expect(select().value).toBe("1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("falls back to Off for a stored value it does not offer", () => {
    window.localStorage.setItem(REFRESH_STORAGE_KEY, "15");
    render(<AutoRefresh />);
    expect(select().value).toBe("off");
  });

  it("renders Off and still lets the choice change when localStorage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const errors: string[] = [];
    const onError = (e: ErrorEvent) => {
      e.preventDefault();
      errors.push(e.message);
    };
    window.addEventListener("error", onError);
    render(<AutoRefresh />);
    expect(select().value).toBe("off");
    fireEvent.change(select(), { target: { value: "1" } });
    window.removeEventListener("error", onError);
    expect(errors).toEqual([]);
    expect(select().value).toBe("1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
