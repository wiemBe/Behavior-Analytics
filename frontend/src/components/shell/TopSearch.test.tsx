// The top bar search: debounce and abort, the result groups, a refused group
// left out and named once, the empty and error states, and the keyboard.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopSearch } from "./TopSearch";
import { ApiError } from "@/lib/api";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
  usePathname: () => "/",
}));

const api = vi.hoisted(() => ({
  searchSources: vi.fn(),
  searchRules: vi.fn(),
  searchOffenses: vi.fn(),
  activeAnomalies: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  shellApi: api,
}));

const page = (items: unknown[]) => Promise.resolve({ items, total: items.length, limit: 5, offset: 0 });
const source = { id: "s1", name: "Core Firewall A", type_name: "Cisco ASA" };
const rule = { id: "r1", qradar_id: 7001, name: "Excessive firewall denies" };
const offense = { qradar_offense_id: 4001, description: "Firewall scan", status: "OPEN" };
const anomaly = (id: string, name: string) => ({
  id,
  log_source_id: `ls-${id}`,
  log_source_name: name,
  anomaly_type: "VOLUME_SPIKE",
  state: "OPEN",
});

function happy() {
  api.searchSources.mockImplementation(() => page([source]));
  api.searchRules.mockImplementation(() => page([rule]));
  api.searchOffenses.mockImplementation(() => page([offense]));
  api.activeAnomalies.mockImplementation(() => page([anomaly("a1", "Core Firewall A"), anomaly("a2", "DNS")]));
}

async function type(value: string) {
  fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  push.mockReset();
  Object.values(api).forEach((f) => f.mockReset());
  happy();
});
afterEach(() => vi.useRealTimers());

describe("debounce and abort", () => {
  it("sends nothing until 250 ms have passed, then one request per list", async () => {
    render(<TopSearch />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fire" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(249);
    });
    expect(api.searchSources).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(api.searchSources).toHaveBeenCalledTimes(1);
    expect(api.searchRules).toHaveBeenCalledTimes(1);
    expect(api.searchOffenses).toHaveBeenCalledTimes(1);
    expect(api.searchSources.mock.calls[0][0]).toBe("fire");
  });

  it("collapses keystrokes inside the window into one search", async () => {
    render(<TopSearch />);
    const box = screen.getByRole("combobox");
    fireEvent.change(box, { target: { value: "fi" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    fireEvent.change(box, { target: { value: "fir" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    expect(api.searchSources).toHaveBeenCalledTimes(1);
    expect(api.searchSources.mock.calls[0][0]).toBe("fir");
  });

  it("aborts the previous request when the text changes", async () => {
    render(<TopSearch />);
    api.searchSources.mockImplementation(() => new Promise(() => {}));
    await type("fi");
    const first = api.searchSources.mock.calls[0][1] as AbortSignal;
    expect(first.aborted).toBe(false);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fir" } });
    expect(first.aborted).toBe(true);
  });

  it("aborts on unmount", async () => {
    const { unmount } = render(<TopSearch />);
    api.searchSources.mockImplementation(() => new Promise(() => {}));
    await type("fi");
    const signal = api.searchSources.mock.calls[0][1] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it("does not search for a single character, and says so", async () => {
    render(<TopSearch />);
    await type("f");
    expect(api.searchSources).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Type at least 2 characters");
  });

  it("reads the anomalies once per session, however many searches", async () => {
    render(<TopSearch />);
    await type("fi");
    await type("fir");
    await type("fire");
    expect(api.activeAnomalies).toHaveBeenCalledTimes(1);
    expect(api.searchSources).toHaveBeenCalledTimes(3);
  });
});

describe("result groups", () => {
  it("shows each group with its links, and the anomaly group labelled first 200", async () => {
    render(<TopSearch />);
    await type("firewall");
    const list = screen.getByRole("listbox");
    expect(within(list).getByRole("group", { name: "Sources" })).toBeInTheDocument();
    expect(within(list).getByRole("group", { name: "Rules" })).toBeInTheDocument();
    expect(within(list).getByRole("group", { name: "Offenses" })).toBeInTheDocument();
    expect(
      within(list).getByRole("group", { name: "Active anomalies (first 200)" }),
    ).toBeInTheDocument();
    const hrefs = screen.getAllByRole("option").map((o) => o.getAttribute("href"));
    expect(hrefs).toEqual([
      "/behavior/sources/s1",
      "/rules/r1",
      "/offenses/4001",
      "/anomalies/a1",
    ]);
  });

  it("filters anomalies by source name on the client, case-insensitively", async () => {
    render(<TopSearch />);
    await type("DNS");
    const group = screen.getByRole("group", { name: "Active anomalies (first 200)" });
    expect(within(group).getAllByRole("option")).toHaveLength(1);
    expect(within(group).getByText("DNS")).toBeInTheDocument();
  });

  it("shows at most five per group", async () => {
    api.searchRules.mockImplementation(() =>
      page(Array.from({ length: 8 }, (_, i) => ({ id: `r${i}`, qradar_id: i, name: `Rule ${i}` }))),
    );
    render(<TopSearch />);
    await type("rule");
    const group = screen.getByRole("group", { name: "Rules" });
    expect(within(group).getAllByRole("option")).toHaveLength(5);
  });
});

describe("a refused group", () => {
  it("is omitted and named once in the footer, never shown as no results", async () => {
    api.searchOffenses.mockRejectedValue(new ApiError(403, "forbidden"));
    render(<TopSearch />);
    await type("firewall");
    expect(screen.queryByRole("group", { name: "Offenses" })).not.toBeInTheDocument();
    expect(
      screen.getByText("Offenses not searched: your role cannot read them"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/no match/i)).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Sources" })).toBeInTheDocument();
  });

  it("names every refused group in the one footer line", async () => {
    api.searchOffenses.mockRejectedValue(new ApiError(403, "forbidden"));
    api.searchRules.mockRejectedValue(new ApiError(403, "forbidden"));
    render(<TopSearch />);
    await type("firewall");
    expect(
      screen.getByText("Rules, Offenses not searched: your role cannot read them"),
    ).toBeInTheDocument();
  });

  it("says nothing was searched when every group is refused", async () => {
    const no = new ApiError(403, "forbidden");
    api.searchSources.mockRejectedValue(no);
    api.searchRules.mockRejectedValue(no);
    api.searchOffenses.mockRejectedValue(no);
    api.activeAnomalies.mockRejectedValue(no);
    render(<TopSearch />);
    await type("firewall");
    expect(screen.getByRole("status")).toHaveTextContent("Nothing was searched");
    expect(screen.queryByText(/No sources, rules/)).not.toBeInTheDocument();
  });
});

describe("empty and error", () => {
  it("says what was searched when nothing matches", async () => {
    api.searchSources.mockImplementation(() => page([]));
    api.searchRules.mockImplementation(() => page([]));
    api.searchOffenses.mockImplementation(() => page([]));
    api.activeAnomalies.mockImplementation(() => page([]));
    render(<TopSearch />);
    await type("zzz");
    expect(screen.getByRole("status")).toHaveTextContent(
      "No sources, rules, offenses or active anomalies match “zzz”.",
    );
  });

  it("says a failed group failed, apart from a group with no match", async () => {
    api.searchRules.mockRejectedValue(new ApiError(500, "boom"));
    api.searchSources.mockImplementation(() => page([]));
    render(<TopSearch />);
    await type("firewall");
    expect(screen.getByText(/Rules could not be searched/)).toBeInTheDocument();
    expect(screen.getByText("No match.")).toBeInTheDocument();
    expect(screen.queryByText(/^No sources, rules/)).not.toBeInTheDocument();
  });

  it("retries a failed anomaly read on the next search instead of caching the failure", async () => {
    api.activeAnomalies.mockRejectedValueOnce(new ApiError(500, "boom"));
    render(<TopSearch />);
    await type("fi");
    await type("fir");
    expect(api.activeAnomalies).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("group", { name: "Active anomalies (first 200)" })).toBeInTheDocument();
  });
});

describe("keyboard", () => {
  it("is a combobox that reports its popup", async () => {
    render(<TopSearch />);
    const box = screen.getByRole("combobox");
    expect(box).toHaveAttribute("aria-expanded", "false");
    await type("firewall");
    expect(box).toHaveAttribute("aria-expanded", "true");
    expect(box).toHaveAttribute("aria-controls", screen.getByRole("listbox").id);
  });

  it("Ctrl+K and Cmd+K focus the field", () => {
    render(<TopSearch />);
    const box = screen.getByRole("combobox");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(box).toHaveFocus();
    box.blur();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(box).toHaveFocus();
  });

  it("moves aria-activedescendant with the arrows, wrapping both ways", async () => {
    render(<TopSearch />);
    await type("firewall");
    const box = screen.getByRole("combobox");
    const options = screen.getAllByRole("option");
    expect(box).not.toHaveAttribute("aria-activedescendant");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box).toHaveAttribute("aria-activedescendant", options[0].id);
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box).toHaveAttribute("aria-activedescendant", options[1].id);
    fireEvent.keyDown(box, { key: "ArrowUp" });
    fireEvent.keyDown(box, { key: "ArrowUp" });
    expect(box).toHaveAttribute("aria-activedescendant", options[options.length - 1].id);
  });

  it("Enter follows the active result and Escape closes", async () => {
    render(<TopSearch />);
    await type("firewall");
    const box = screen.getByRole("combobox");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(push).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/rules/r1");

    await type("firewall");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(box).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
