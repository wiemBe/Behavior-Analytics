// The assembled bar and the shell around it: one read of /overview for two
// parts, a refused read reaching the right part, and the shell's markup rules.

import { act, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "./TopBar";
import RootLayout from "@/app/layout";
import { ApiError } from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/behavior",
}));

const api = vi.hoisted(() => ({
  healthReady: vi.fn(),
  overview: vi.fn(),
  searchCadence: vi.fn(),
  searchSources: vi.fn(),
  searchRules: vi.fn(),
  searchOffenses: vi.fn(),
  activeAnomalies: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  shellApi: api,
}));

const flush = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

beforeEach(() => {
  vi.useFakeTimers();
  Object.values(api).forEach((f) => f.mockReset());
  api.healthReady.mockResolvedValue({ status: "ready", database: "ok" });
  api.overview.mockResolvedValue({
    instance_status: "HEALTHY",
    instance_version: "7.5",
    instance_last_health_check_at: "2026-10-14T08:55:00Z",
    alerts: { open: 4, acknowledged: 0, by_severity: {} },
  });
  api.searchCadence.mockResolvedValue([]);
});
afterEach(() => vi.useRealTimers());

describe("TopBar", () => {
  it("reads /overview once for both the bell and the instance signal", async () => {
    render(<TopBar />);
    await flush();
    expect(api.overview).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Open alerts: 4" })).toBeInTheDocument();
  });

  it("aborts its reads on unmount", async () => {
    const { unmount } = render(<TopBar />);
    const signal = api.overview.mock.calls[0][0] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it("a refused overview leaves the bell without a number, not at zero", async () => {
    api.overview.mockRejectedValue(new ApiError(403, "forbidden"));
    render(<TopBar />);
    await flush();
    const bell = screen.getByRole("link", { name: "Open alerts: unavailable" });
    expect(bell.textContent).not.toMatch(/\d/);
  });

  it("a failed overview does the same", async () => {
    api.overview.mockRejectedValue(new ApiError(500, "boom"));
    render(<TopBar />);
    await flush();
    expect(screen.getByRole("link", { name: "Open alerts: unavailable" })).toBeInTheDocument();
  });

  it("re-reads on an auto-refresh and keeps the old count meanwhile", async () => {
    render(<TopBar />);
    await flush();
    api.overview.mockReturnValue(new Promise(() => {}));
    fireEventChange("1");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("link", { name: "Open alerts: 4" })).toBeInTheDocument();
  });

  it("holds no heading", async () => {
    render(<TopBar />);
    await flush();
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });
});

import { fireEvent } from "@testing-library/react";
function fireEventChange(value: string) {
  fireEvent.change(screen.getByRole("combobox", { name: "Refresh" }), { target: { value } });
}

describe("the shell", () => {
  const html = () =>
    renderToStaticMarkup(
      <RootLayout>
        <h1>Page title</h1>
      </RootLayout>,
    );

  it("server-renders without touching window, and holds no heading of its own", () => {
    const markup = html();
    expect(markup.match(/<h[1-6][\s>]/g)).toEqual(["<h1>"]);
  });

  it("makes the brand a link, inside the header above the sidebar", () => {
    const doc = new DOMParser().parseFromString(html(), "text/html");
    const brand = doc.querySelector("header.shell-head > a.brand");
    expect(brand?.tagName).toBe("A");
    expect(brand?.getAttribute("href")).toBe("/behavior");
    expect(brand?.textContent).toContain("Behavior Analytics");
    expect(doc.querySelector("header.shell-head .topbar")).not.toBeNull();
    expect(doc.querySelector(".layout > aside.sidebar")).not.toBeNull();
    expect(doc.querySelector(".layout > main#content")).not.toBeNull();
  });

  it("renders no time-range control and no user menu", () => {
    const text = new DOMParser().parseFromString(html(), "text/html").body.textContent ?? "";
    expect(text).not.toMatch(/time range|sign out|profile/i);
  });
});
