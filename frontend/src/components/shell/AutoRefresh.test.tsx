// Auto-refresh: Off does nothing, the timer calls router.refresh, it pauses on a
// hidden tab and under a dialog, and the times are UTC and only what is known.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AutoRefresh,
  DataAgeProvider,
  formatClockUtc,
  useRefreshTick,
  useReportDataAge,
} from "./AutoRefresh";

const refresh = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn() }) }));

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-14T14:02:11Z"));
  refresh.mockReset();
});
afterEach(() => {
  setHidden(false);
  vi.useRealTimers();
  document.body.innerHTML = "";
});

const choose = (value: string) =>
  fireEvent.change(screen.getByRole("combobox", { name: "Refresh" }), { target: { value } });
const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

describe("AutoRefresh", () => {
  it("offers Off, 1 min and 5 min, defaulting to Off", () => {
    render(<AutoRefresh />);
    const select = screen.getByRole("combobox", { name: "Refresh" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(["Off", "1 min", "5 min"]);
    expect(select.value).toBe("off");
  });

  it("does nothing while Off", async () => {
    render(<AutoRefresh />);
    await tick(10 * 60_000);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("calls router.refresh every minute on 1 min, and tells the bar to re-read", async () => {
    const onRefresh = vi.fn();
    render(<AutoRefresh onRefresh={onRefresh} />);
    choose("1");
    await tick(59_999);
    expect(refresh).not.toHaveBeenCalled();
    await tick(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await tick(60_000);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("waits five minutes on 5 min, and stops when set back to Off", async () => {
    render(<AutoRefresh />);
    choose("5");
    await tick(240_000);
    expect(refresh).not.toHaveBeenCalled();
    await tick(60_000);
    expect(refresh).toHaveBeenCalledTimes(1);
    choose("off");
    await tick(10 * 60_000);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("pauses while the tab is hidden and resumes when it is visible", async () => {
    render(<AutoRefresh />);
    choose("1");
    act(() => setHidden(true));
    await tick(5 * 60_000);
    expect(refresh).not.toHaveBeenCalled();
    act(() => setHidden(false));
    await tick(60_000);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("skips a refresh while a dialog is open", async () => {
    render(<AutoRefresh />);
    choose("1");
    const dialog = document.createElement("div");
    dialog.setAttribute("aria-modal", "true");
    document.body.appendChild(dialog);
    await tick(60_000);
    expect(refresh).not.toHaveBeenCalled();
    dialog.remove();
    await tick(60_000);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("stops its timer on unmount", async () => {
    const { unmount } = render(<AutoRefresh />);
    choose("1");
    unmount();
    await tick(5 * 60_000);
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe("the times", () => {
  it("shows the last-refreshed time in UTC, with the zone", () => {
    render(<AutoRefresh />);
    expect(screen.getByText(/Refreshed/)).toHaveTextContent("Refreshed 14:02:11 UTC");
  });

  it("moves the last-refreshed time on a refresh", async () => {
    render(<AutoRefresh />);
    choose("1");
    await tick(60_000);
    expect(screen.getByText(/Refreshed/)).toHaveTextContent("Refreshed 14:03:11 UTC");
  });

  it("formats in UTC whatever the zone of the input", () => {
    expect(formatClockUtc("2026-10-14T13:55:00+03:00")).toBe("10:55:00 UTC");
    expect(formatClockUtc("not a date")).toBe("");
  });

  it("shows no data age until a page supplies one", () => {
    render(<AutoRefresh />);
    expect(screen.queryByText(/Data to/)).not.toBeInTheDocument();
  });

  it("shows the data age a page supplies, and drops it when the page goes", () => {
    function Page() {
      useReportDataAge("2026-10-14T13:55:00Z");
      return null;
    }
    const { rerender } = render(
      <DataAgeProvider>
        <AutoRefresh />
        <Page />
      </DataAgeProvider>,
    );
    expect(screen.getByText(/Data to/)).toHaveTextContent("Data to 13:55:00 UTC");
    rerender(
      <DataAgeProvider>
        <AutoRefresh />
      </DataAgeProvider>,
    );
    expect(screen.queryByText(/Data to/)).not.toBeInTheDocument();
  });
});

describe("the refresh tick", () => {
  function Reader() {
    return <output data-testid="tick">{useRefreshTick()}</output>;
  }

  it("starts at 0 and moves once per automatic refresh", async () => {
    render(
      <DataAgeProvider>
        <AutoRefresh />
        <Reader />
      </DataAgeProvider>,
    );
    expect(screen.getByTestId("tick")).toHaveTextContent("0");
    choose("1");
    await tick(60_000);
    expect(screen.getByTestId("tick")).toHaveTextContent("1");
    await tick(60_000);
    expect(screen.getByTestId("tick")).toHaveTextContent("2");
  });

  it("does not move while Off, or while a dialog is open", async () => {
    render(
      <DataAgeProvider>
        <AutoRefresh />
        <Reader />
      </DataAgeProvider>,
    );
    await tick(10 * 60_000);
    expect(screen.getByTestId("tick")).toHaveTextContent("0");
    choose("1");
    const dialog = document.createElement("div");
    dialog.setAttribute("aria-modal", "true");
    document.body.appendChild(dialog);
    await tick(60_000);
    expect(screen.getByTestId("tick")).toHaveTextContent("0");
  });
});
