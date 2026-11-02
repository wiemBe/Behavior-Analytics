import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { ArielLoadCurrent } from "@/lib/api";

import en from "../../../messages/en";
import { current, health, notRead, summary, unreachableHealth } from "./fixtures";
import { SiemLoadPage } from "./SiemLoadPage";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown>, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => {
    mocks.keys.push(key);
    return mocks.states[key] ?? { status: "loading" };
  },
}));
const dataAge = vi.hoisted(() => ({ calls: [] as (string | null)[] }));
vi.mock("@/components/shell/AutoRefresh", () => ({
  useReportDataAge: (iso: string | null) => dataAge.calls.push(iso),
}));

const CURRENT = "/admin/ariel-load/current";
const SUMMARY = "/admin/ariel-load?hours=24";

const okState = <T,>(data: T, over: Record<string, unknown> = {}): ResourceState<T> => ({
  status: "ok",
  data,
  receivedAt: "2026-11-02T10:15:04Z",
  refreshing: false,
  refreshFailed: null,
  ...over,
});

beforeEach(() => {
  mocks.states = {};
  mocks.keys = [];
  dataAge.calls = [];
  mocks.states[SUMMARY] = okState(summary());
});

describe("SiemLoadPage: the first screen", () => {
  it("answers 'how much do we load QRadar and how is it doing' before any table below", () => {
    mocks.states[CURRENT] = okState(current());
    render(<SiemLoadPage range="24h" />);
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings.slice(0, 3)).toEqual(["Our load on QRadar", "QRadar", "qradar-lab"]);
    expect(headings.slice(3)).toEqual(["Searches in flight", "Load by caller"]);
  });

  it("leaves the page heading to the header: the body holds no h1", () => {
    mocks.states[CURRENT] = okState(current());
    render(<SiemLoadPage range="24h" />);
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });

  it("tells the top bar how far the data reaches", () => {
    mocks.states[CURRENT] = okState(current());
    render(<SiemLoadPage range="24h" />);
    expect(dataAge.calls.at(-1)).toBe("2026-11-02T10:15:03Z");
  });

  it("hands the top bar no data age while it has no answer", () => {
    render(<SiemLoadPage range="24h" />);
    expect(dataAge.calls.at(-1)).toBeNull();
  });

  it("uses no 'live' or 'real-time' wording anywhere on the page", () => {
    mocks.states[CURRENT] = okState(current({ qradar: { available: true, consoles: [health(), unreachableHealth({ instance_id: "x", instance_name: "dr" })] } }));
    const { container } = render(<SiemLoadPage range="24h" />);
    expect(container.textContent ?? "").not.toMatch(/\blive\b|real[- ]?time/i);
  });
});

describe("SiemLoadPage: states", () => {
  it("says it is loading", () => {
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the SIEM load");
  });

  it("names admin:* when the role cannot read it, and reads no summary into view", () => {
    mocks.states[CURRENT] = { status: "forbidden", httpStatus: 403 };
    render(<SiemLoadPage range="24h" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The SIEM load could not be read.");
    expect(alert).toHaveTextContent("permission admin:*");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it.each([
    [500, "The backend answered with HTTP 500."],
    [null, "The backend did not answer."],
    [401, "Your session has expired."],
  ])("says what failed and what to do when the read fails (%s)", (httpStatus, sentence) => {
    mocks.states[CURRENT] = { status: "error", httpStatus };
    render(<SiemLoadPage range="24h" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The SIEM load could not be read.");
    expect(alert).toHaveTextContent(sentence);
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says a rate limit is a rate limit", () => {
    mocks.states[CURRENT] = { status: "error", httpStatus: 429, retryAfter: null };
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Too many requests came from this address");
  });

  it("reads again under a new request when Try again is chosen", () => {
    mocks.states[CURRENT] = { status: "error", httpStatus: 500 };
    render(<SiemLoadPage range="24h" />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    const reads = mocks.keys.filter((key) => key.startsWith(CURRENT));
    expect(reads.at(-1)).not.toBe(CURRENT);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the SIEM load");
  });

  it("starts the summary read with the first one instead of waiting for it", () => {
    render(<SiemLoadPage range="24h" />);
    expect(mocks.keys).toContain(CURRENT);
    expect(mocks.keys).toContain(SUMMARY);
  });

  it("reads no summary for a role that cannot read the load", () => {
    mocks.states[CURRENT] = { status: "forbidden", httpStatus: 403 };
    render(<SiemLoadPage range="24h" />);
    expect(mocks.keys).not.toContain(SUMMARY);
  });

  it("keeps the last answer and says the refresh failed, with the time the answer is from", () => {
    mocks.states[CURRENT] = okState(current(), { refreshFailed: { httpStatus: 500 } });
    render(<SiemLoadPage range="24h" />);
    const note = screen.getByText(/The latest refresh failed/);
    expect(note).toHaveTextContent("(HTTP 500)");
    expect(note).toHaveTextContent("2026-11-02 10:15:03 UTC");
    expect(screen.getByText("Our load on QRadar")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /last 5, 15 and 60 minutes/ })).toBeInTheDocument();
  });

  it("says a failed refresh was rate limited, with the wait", () => {
    mocks.states[CURRENT] = okState(current(), { refreshFailed: { httpStatus: 429, retryAfter: 9 } });
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByText(/The latest refresh failed/)).toHaveTextContent("try again in 9");
  });

  it("shows no refresh note when the last refresh worked", () => {
    mocks.states[CURRENT] = okState(current());
    render(<SiemLoadPage range="24h" />);
    expect(screen.queryByText(/refresh failed/)).not.toBeInTheDocument();
  });

  it("shows each part that is unavailable as unavailable and the others as they are", () => {
    mocks.states[CURRENT] = okState(
      current({
        in_flight: { available: false, reason: "in-flight searches could not be read" },
        qradar: { available: false, reason: "the registered consoles could not be read" },
      }),
    );
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByText("QRadar's health could not be read, so no figure is shown in its place.")).toBeInTheDocument();
    expect(screen.getByText("The searches in flight could not be read, so no figure is shown in its place.")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /last 5, 15 and 60 minutes/ })).toBeInTheDocument();
  });

  it("shows no console when none is configured, and still shows our own load", () => {
    mocks.states[CURRENT] = okState(current({ qradar: { available: true, consoles: [] } }));
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByText("No QRadar console is enabled.")).toBeInTheDocument();
    expect(screen.getByText("Our load on QRadar")).toBeInTheDocument();
  });

  it("does not turn an old response with no QRadar part into a healthy console", () => {
    const old = current() as Partial<ArielLoadCurrent>;
    delete old.qradar;
    mocks.states[CURRENT] = okState(old as ArielLoadCurrent);
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByText("QRadar's health could not be read, so no figure is shown in its place.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /^Console/ })).not.toBeInTheDocument();
  });

  it("shows a degraded console: unreachable, FAILED fields, a host down and an over-allocated license", () => {
    const degraded = unreachableHealth();
    mocks.states[CURRENT] = okState(
      current({
        qradar: {
          available: true,
          consoles: [
            degraded,
            health({
              instance_id: "z",
              instance_name: "qradar-dr",
              license: notRead("FAILED", "QRadar answered with a server error.", "upstream_error"),
            }),
          ],
        },
      }),
    );
    render(<SiemLoadPage range="24h" />);
    expect(screen.getByText("Not reachable")).toBeInTheDocument();
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(2);
    expect(screen.getByText("QRadar answered with a server error.")).toBeInTheDocument();
  });
});

describe("SiemLoadPage: nothing in the catalog says live or real-time", () => {
  it("holds neither word in any siemLoad value or the guide section", () => {
    const flat = JSON.stringify([en.siemLoad, en.guide.sections["siem-load"]]);
    expect(flat).not.toMatch(/\blive\b|real[- ]?time/i);
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SiemLoadPage: the top bar's Refresh drives it", () => {
  it("reads again when the shell's refresh tick moves, and not before", async () => {
    vi.resetModules();
    vi.doUnmock("@/hooks/useApiResource");
    vi.doUnmock("@/components/shell/AutoRefresh");
    vi.doMock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));
    vi.useFakeTimers();
    const api = await import("@/lib/api");
    const read = vi.spyOn(api.arielLoadApi, "current").mockResolvedValue(current());
    vi.spyOn(api.arielLoadApi, "summary").mockResolvedValue(summary());
    const { AutoRefresh, DataAgeProvider } = await import("@/components/shell/AutoRefresh");
    const { SiemLoadPage: Page } = await import("./SiemLoadPage");

    render(
      <DataAgeProvider>
        <AutoRefresh />
        <Page range="24h" />
      </DataAgeProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(read).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByRole("combobox", { name: "Refresh" }), { target: { value: "1" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_999);
    });
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Our load on QRadar")).toBeInTheDocument();
    vi.doUnmock("next/navigation");
  });
});
