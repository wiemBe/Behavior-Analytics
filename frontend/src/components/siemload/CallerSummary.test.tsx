import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { ArielLoadSummary } from "@/lib/api";

import { caller, EMPTY_SUMMARY, summary } from "./fixtures";
import { CallerSummary } from "./CallerSummary";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown>, keys: [] as string[] }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => {
    mocks.keys.push(key);
    return mocks.states[key] ?? { status: "loading" };
  },
}));

const okState = (data: ArielLoadSummary): ResourceState<ArielLoadSummary> => ({
  status: "ok",
  data,
  receivedAt: "2026-11-02T10:15:04Z",
  refreshing: false,
  refreshFailed: null,
});

beforeEach(() => {
  mocks.states = {};
  mocks.keys = [];
});

function rowOf(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(`^${name}`) });
}
const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((c) => c.textContent);

describe("CallerSummary: the access check's cancelled probe", () => {
  const accessCheck = caller({
    caller: "ACCESS_CHECK",
    searches: 5,
    outcomes: { CANCELED: 4, ERROR: 1 },
  });
  const history = caller({ caller: "HISTORY", searches: 4, outcomes: { CANCELED: 4 } });
  const note = /a cancelled probe is its normal end and is not counted in Failed/;

  it("does not count a cancelled probe as failed, but still counts its error", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary({ callers: [accessCheck] }));
    render(<CallerSummary range="24h" />);
    expect(cells(rowOf("Access check")).slice(0, 5)).toEqual(["5", "0", "1", "0", "0"]);
  });

  it("counts a cancelled search of any other caller as failed", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary({ callers: [history] }));
    render(<CallerSummary range="24h" />);
    expect(cells(rowOf("History")).slice(0, 5)).toEqual(["4", "0", "4", "0", "0"]);
  });

  it("explains the rule only when the access check row is there", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary({ callers: [history] }));
    const { unmount } = render(<CallerSummary range="24h" />);
    expect(screen.queryByText(note)).not.toBeInTheDocument();
    unmount();
    mocks.states["/admin/ariel-load?hours=24"] = okState(
      summary({ callers: [accessCheck, history] }),
    );
    render(<CallerSummary range="24h" />);
    expect(screen.getByText(note)).toBeInTheDocument();
  });
});

describe("CallerSummary: the table", () => {
  it("reads the window the range names and lists the columns the analyst asked for", () => {
    mocks.states["/admin/ariel-load?hours=168"] = okState(summary({ hours: 168 }));
    render(<CallerSummary range="7d" />);
    expect(mocks.keys).toContain("/admin/ariel-load?hours=168");
    const table = screen.getByRole("table", { name: "Load by caller" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Caller",
      "Searches",
      "Completed",
      "Failed",
      "Refused",
      "Timeouts",
      "Wall time p50 / p95",
      "QRadar execution total / p95",
      "Records returned",
    ]);
  });

  it("shows each caller by its readable name with completed, failed, refused and timeouts counted", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary());
    render(<CallerSummary range="24h" />);
    // 3 ERROR + 2 TIMEOUT are failed; the 2 timeouts are also shown on their own.
    expect(cells(rowOf("Log source volume")).slice(0, 7)).toEqual(["288", "280", "5", "3", "2", "760 ms3.1 s", "4 min 4 s2.8 s"]);
    // PROVIDER_UNAVAILABLE counts as failed too.
    expect(cells(rowOf("WAF collection")).slice(0, 5)).toEqual(["96", "90", "6", "0", "0"]);
  });

  it("counts an abandoned search with the failures and the footnote says what it means", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(
      summary({
        callers: [
          caller({
            caller: "IPS_COLLECTION",
            searches: 10,
            outcomes: { COMPLETED: 6, ERROR: 1, ABANDONED: 3 },
          }),
        ],
      }),
    );
    render(<CallerSummary range="24h" />);
    expect(cells(rowOf("IPS collection")).slice(0, 5)).toEqual(["10", "6", "4", "0", "0"]);
    expect(
      screen.getByText(/Abandoned searches are counted in Failed: their worker stopped before it recorded an end/),
    ).toBeInTheDocument();
  });

  it("right-aligns every figure column and gives each figure its unit", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary());
    render(<CallerSummary range="24h" />);
    const row = rowOf("Log source volume");
    for (const cell of within(row).getAllByRole("cell")) expect(cell).toHaveClass("num");
    expect(within(row).getByText("4 min 4 s")).toBeInTheDocument();
    expect(within(row).getByText("2.8 s")).toBeInTheDocument();
  });

  it("marks a total as a lower bound when some searches reported no figure, and counts them", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary());
    render(<CallerSummary range="24h" />);
    const row = cells(rowOf("WAF collection"));
    expect(row[6]).toContain("≥ 3 min 8 s4 unreported");
    expect(row[7]).toBe("≥ 55,0004 unreported");
  });

  it("shows a total nobody reported as a dash, never 0", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary());
    render(<CallerSummary range="24h" />);
    const row = cells(rowOf("Rule matches"));
    expect(row[7]).toBe("—");
    expect(row[6]).toMatch(/^—/);
    expect(row[6]).not.toMatch(/^0/);
  });

  it("stacks each related pair in one cell: both figures carry a unit, a missing one is a dash", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(
      summary({
        callers: [
          caller({
            caller: "HISTORY",
            wall_ms_p50: 760,
            wall_ms_p95: null,
            qradar_execution_ms_sum: 244_000,
            qradar_execution_ms_p95: null,
          }),
        ],
      }),
    );
    render(<CallerSummary range="24h" />);
    const row = rowOf("History backfill");
    const [wall, execution] = within(row).getAllByRole("cell").slice(5, 7);
    expect(Array.from(wall.querySelectorAll(".sl-pairfig")).map((n) => n.textContent)).toEqual(["760 ms", "—"]);
    expect(Array.from(execution.querySelectorAll(".sl-pairfig")).map((n) => n.textContent)).toEqual([
      "4 min 4 s",
      "—",
    ]);
    expect(within(row).getAllByRole("cell")).toHaveLength(8);
  });

  it("shows a caller it does not know as its code, never blank", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary({ callers: [caller({ caller: "NEW_ONE" })] }));
    render(<CallerSummary range="24h" />);
    expect(screen.getByRole("rowheader", { name: "NEW_ONE" })).toBeInTheDocument();
  });

  it("states that percentiles cover completed searches only", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary());
    render(<CallerSummary range="24h" />);
    expect(screen.getByText(/Percentiles cover completed searches only/)).toBeInTheDocument();
  });

  it("shows the window it covers in UTC", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary());
    render(<CallerSummary range="24h" />);
    expect(screen.getByText(/^From/)).toHaveTextContent("From 2026-11-01 10:15:03 UTC to 2026-11-02 10:15:03 UTC");
  });
});

describe("CallerSummary: the range control", () => {
  it("offers 1 h, 24 h, 7 d and 30 d as links, and marks the current one", () => {
    mocks.states["/admin/ariel-load?hours=720"] = okState(summary({ hours: 720 }));
    render(<CallerSummary range="30d" />);
    const nav = screen.getByRole("navigation", { name: "Summary window" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["1 h", "24 h", "7 d", "30 d"]);
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/admin/siem-load?range=1h",
      "/admin/siem-load",
      "/admin/siem-load?range=7d",
      "/admin/siem-load?range=30d",
    ]);
    expect(within(nav).getByRole("link", { name: "30 d" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "24 h" })).not.toHaveAttribute("aria-current");
  });
});

describe("CallerSummary: states", () => {
  it("says it is loading", () => {
    render(<CallerSummary range="24h" />);
    expect(screen.getByText(/Loading the per-caller summary/)).toBeInTheDocument();
  });

  it("says no search was recorded in the window, with no table and no zeros", () => {
    mocks.states["/admin/ariel-load?hours=168"] = okState({ ...EMPTY_SUMMARY, hours: 168 });
    render(<CallerSummary range="7d" />);
    expect(screen.getByText("No search was recorded in the last 7 d.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("treats a window with no callers as empty even if the flag is missing", () => {
    mocks.states["/admin/ariel-load?hours=24"] = okState(summary({ empty: false, callers: [] }));
    render(<CallerSummary range="24h" />);
    expect(screen.getByText("No search was recorded in the last 24 h.")).toBeInTheDocument();
  });

  it("names the permission when the read is forbidden", () => {
    mocks.states["/admin/ariel-load?hours=24"] = { status: "forbidden", httpStatus: 403 };
    render(<CallerSummary range="24h" />);
    expect(screen.getByRole("alert")).toHaveTextContent("permission admin:*");
    expect(screen.getByRole("alert")).toHaveTextContent("Nothing was changed");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it.each([
    [500, "The backend answered with HTTP 500."],
    [null, "The backend did not answer."],
  ])("says what failed and what to do when the read fails (%s)", (httpStatus, sentence) => {
    mocks.states["/admin/ariel-load?hours=24"] = { status: "error", httpStatus };
    render(<CallerSummary range="24h" />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The per-caller summary could not be read.");
    expect(alert).toHaveTextContent(sentence);
    expect(alert).toHaveTextContent("Check that the backend is reachable");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says a rate-limited read was rate-limited, with the wait", () => {
    mocks.states["/admin/ariel-load?hours=24"] = { status: "error", httpStatus: 429, retryAfter: 12 };
    render(<CallerSummary range="24h" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in 12");
  });

  it("keeps the range control when the read fails", () => {
    mocks.states["/admin/ariel-load?hours=24"] = { status: "error", httpStatus: 500 };
    render(<CallerSummary range="24h" />);
    expect(screen.getByRole("navigation", { name: "Summary window" })).toBeInTheDocument();
  });
});
