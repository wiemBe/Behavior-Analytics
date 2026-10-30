import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchCadenceTable } from "./SearchCadenceTable";
import { ApiError, api, type SearchCadenceItem } from "@/lib/api";

const ALLOWED = [300, 900, 1800, 3600, 7200, 14400, 28800];

function item(over: Partial<SearchCadenceItem> = {}): SearchCadenceItem {
  return {
    family: "PHASE_A_VOLUME",
    cadence_seconds: 3600,
    allowed_cadences_seconds: ALLOWED,
    bucket_seconds: 300,
    settle_seconds: 120,
    last_slot_start: "2026-10-12T09:00:00Z",
    next_dispatch_at: "2026-10-12T10:00:00Z",
    updated_at: "2026-10-12T08:00:00Z",
    updated_by: null,
    enabled: true,
    last_run: {
      at: "2026-10-12T09:00:20Z",
      outcome: "ok",
      searches_sent: 1,
      buckets_collected: 12,
      split_count: 0,
    },
    ...over,
  };
}

function rowOf(name: string): HTMLElement {
  return screen.getByRole("row", { name: new RegExp(name, "i") });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("rows", () => {
  it("shows one row per family with a human name and scope", () => {
    render(
      <SearchCadenceTable
        items={[
          item(),
          item({ family: "WAF_COLLECTION" }),
          item({ family: "ANOMALY_EXPLANATION", bucket_seconds: null }),
          item({ family: "RULE_MATCH" }),
        ]}
      />,
    );
    expect(screen.getAllByRole("row")).toHaveLength(5); // header + 4
    expect(screen.getByText("Phase A volume")).toBeInTheDocument();
    expect(screen.getByText("Event count per log source")).toBeInTheDocument();
    expect(screen.getByText("Rule matches")).toBeInTheDocument();
  });

  it("offers the seven presets in each select, labelled 5 min … 8 h", () => {
    render(<SearchCadenceTable items={[item()]} />);
    const select = screen.getByRole("combobox", { name: "Cadence for Phase A volume" });
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "5 min", "15 min", "30 min", "1 h", "2 h", "4 h", "8 h",
    ]);
    expect(select).toHaveValue("3600");
  });

  it("computes the worst-case delay as cadence + bucket + settle", () => {
    render(<SearchCadenceTable items={[item()]} />);
    expect(within(rowOf("Phase A volume")).getByText("up to 1 h 7 min")).toBeInTheDocument();
  });

  it("words the delay of a bucketless family as evidence arrival", () => {
    render(
      <SearchCadenceTable
        items={[item({ family: "ANOMALY_EXPLANATION", bucket_seconds: null })]}
      />,
    );
    const row = rowOf("Anomaly explanations");
    expect(within(row).getByText("evidence up to 1 h 2 min")).toBeInTheDocument();
    // No bucket reads as a dash, and the header says what that means.
    expect(within(row).getByTitle("This family has no buckets")).toHaveTextContent("—");
    expect(screen.getByRole("columnheader", { name: /^bucket/i })).toHaveTextContent(
      /no buckets/i,
    );
  });

  it("shows times in UTC with the zone", () => {
    render(<SearchCadenceTable items={[item()]} />);
    const row = rowOf("Phase A volume");
    // Compact in the cell, with the full time (seconds included) on hover.
    expect(within(row).getByText("10-12 09:00 UTC")).toHaveAttribute(
      "title",
      "2026-10-12 09:00:20 UTC",
    );
    expect(within(row).getByText("10-12 10:00 UTC")).toHaveAttribute(
      "title",
      "2026-10-12 10:00:00 UTC",
    );
  });

  it("says 'Not run since upgrade' for a family that never ran, never a zero", () => {
    render(<SearchCadenceTable items={[item({ last_run: null })]} />);
    const row = rowOf("Phase A volume");
    expect(within(row).getByText("Not run since upgrade")).toBeInTheDocument();
    expect(within(row).getByText("No run yet")).toBeInTheDocument();
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    // A past computed instant for a family that has never run is not a next run.
    expect(within(row).getByText("—")).toHaveAttribute(
      "title",
      "This family has not run, and no future run is scheduled.",
    );
    expect(within(row).queryByText("10-12 10:00 UTC")).not.toBeInTheDocument();
  });

  it("shows an em dash for a disabled family even when the next time is in the future", () => {
    render(
      <SearchCadenceTable
        items={[item({ enabled: false, next_dispatch_at: "2099-01-01T00:00:00Z" })]}
      />,
    );
    const row = rowOf("Phase A volume");
    expect(within(row).getByText("—")).toHaveAttribute(
      "title",
      "This family is off, so it has no next run.",
    );
    expect(within(row).queryByText("01-01 00:00 UTC")).not.toBeInTheDocument();
  });

  it("shows a future next run for an enabled family that has never run", () => {
    render(
      <SearchCadenceTable
        items={[item({ last_run: null, next_dispatch_at: "2099-01-01T00:00:00Z" })]}
      />,
    );
    expect(within(rowOf("Phase A volume")).getByText("01-01 00:00 UTC")).toBeInTheDocument();
  });

  it("says 'not counted' for a null searches_sent, and keeps a real zero as 0", () => {
    const first = render(
      <SearchCadenceTable
        items={[item({ last_run: { ...item().last_run!, searches_sent: null } })]}
      />,
    );
    const counted = within(rowOf("Phase A volume"));
    expect(counted.getByText("not counted")).toHaveAttribute(
      "title",
      expect.stringMatching(/does not count its searches/i),
    );
    expect(counted.queryByText("0")).not.toBeInTheDocument();

    first.unmount();
    render(
      <SearchCadenceTable
        items={[item({ last_run: { ...item().last_run!, searches_sent: 0 } })]}
      />,
    );
    expect(within(rowOf("Phase A volume")).getByText("0")).toBeInTheDocument();
  });

  it("right-aligns and tabulates the numeric columns", () => {
    render(<SearchCadenceTable items={[item()]} />);
    const headers = screen.getAllByRole("columnheader");
    const numeric = headers.filter((h) => h.classList.contains("num"));
    expect(numeric.map((h) => h.textContent)).toEqual([
      expect.stringMatching(/^Bucket/),
      expect.stringMatching(/^Worst-case detection delay/),
      "Last run (UTC)",
      "Next run (UTC)",
      "Searches in last run",
    ]);
  });
});

describe("last-run outcome", () => {
  const withOutcome = (outcome: string) =>
    render(<SearchCadenceTable items={[item({ last_run: { ...item().last_run!, outcome } })]} />);

  it.each([
    ["ok", "Complete"],
    ["partial", "Partial"],
    ["failed", "Failed"],
    ["no-instance", "No enabled instance"],
    ["no-sources", "No eligible WAF source"],
    ["dispatched", "Dispatched"],
  ])("labels the recorded outcome %s as %s", (raw, label) => {
    withOutcome(raw);
    const row = rowOf("Phase A volume");
    expect(within(row).getByText(label)).toBeInTheDocument();
    expect(within(row).queryByText(raw)).not.toBeInTheDocument();
  });

  it("tones the outcomes without relying on tone alone", () => {
    withOutcome("failed");
    expect(within(rowOf("Phase A volume")).getByText("Failed")).toHaveClass("crit");
  });

  it("shows an unknown outcome verbatim in code, never guessed", () => {
    withOutcome("quota-exceeded");
    const code = within(rowOf("Phase A volume")).getByText("quota-exceeded");
    expect(code.tagName).toBe("CODE");
  });

  it("shows no outcome line when none was recorded", () => {
    render(
      <SearchCadenceTable
        items={[item({ last_run: { ...item().last_run!, outcome: null } })]}
      />,
    );
    expect(within(rowOf("Phase A volume")).queryByText("Complete")).not.toBeInTheDocument();
  });
});

describe("changing a cadence", () => {
  it("shows a pending state, then the new value and who changed it", async () => {
    let resolve!: (v: SearchCadenceItem) => void;
    const put = vi.spyOn(api, "setSearchCadence").mockReturnValue(
      new Promise<SearchCadenceItem>((r) => {
        resolve = r;
      }),
    );
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    const select = screen.getByRole("combobox", { name: "Cadence for Phase A volume" });

    await user.selectOptions(select, "2 h");

    expect(put).toHaveBeenCalledWith("PHASE_A_VOLUME", 7200);
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
    expect(select).toBeDisabled();
    expect(select).toHaveValue("7200");

    resolve(
      item({
        cadence_seconds: 7200,
        updated_by: "admin@lab",
        updated_at: "2026-10-12T09:30:00Z",
      }),
    );
    await waitFor(() => expect(select).toBeEnabled());
    expect(select).toHaveValue("7200");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Saved. Changed by admin@lab at 2026-10-12 09:30:00 UTC.",
    );
    // The delay follows the new cadence: 2 h + 5 min + 2 min.
    expect(screen.getByText("up to 2 h 7 min")).toBeInTheDocument();
  });

  it("reverts the select and names the permission on a 403", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    const select = screen.getByRole("combobox", { name: "Cadence for Phase A volume" });

    await user.selectOptions(select, "15 min");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/cannot change search cadence/i);
    expect(alert).toHaveTextContent("admin:search_cadence");
    expect(select).toHaveValue("3600");
    expect(select).toBeEnabled();
    expect(screen.getByText("up to 1 h 7 min")).toBeInTheDocument();
  });

  it("reverts the select and says what failed on any other error", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(500, "internal"));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    const select = screen.getByRole("combobox", { name: "Cadence for Phase A volume" });

    await user.selectOptions(select, "8 h");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/not changed/i);
    expect(alert).toHaveTextContent(/try again/i);
    expect(alert).not.toHaveTextContent("admin:search_cadence");
    expect(select).toHaveValue("3600");
  });

  it("reverts on a network failure too", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new TypeError("fetch failed"));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    const select = screen.getByRole("combobox", { name: "Cadence for Phase A volume" });
    await user.selectOptions(select, "5 min");
    expect(await screen.findByRole("alert")).toHaveTextContent(/not changed/i);
    expect(select).toHaveValue("3600");
  });

  it("changes only the row it was made on", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(403, "no"));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item(), item({ family: "RULE_MATCH" })]} />);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Cadence for Rule matches" }),
      "4 h",
    );
    await screen.findByRole("alert");
    expect(within(rowOf("Phase A volume")).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts the outcome in a full-width row directly under its family's row", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item(), item({ family: "RULE_MATCH" })]} />);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Cadence for Phase A volume" }),
      "15 min",
    );
    const alert = await screen.findByRole("alert");
    const cell = alert.closest("td")!;
    expect(cell).toHaveAttribute("colspan", "7");
    const rows = screen.getAllByRole("row");
    expect(rows[rows.indexOf(cell.closest("tr")!) - 1]).toBe(rowOf("Phase A volume"));
    // The status is not inside the narrow cadence cell.
    expect(within(rowOf("Phase A volume")).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("sets the permission name in unbroken code", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Cadence for Phase A volume" }),
      "15 min",
    );
    const perm = await screen.findByText("admin:search_cadence");
    expect(perm.tagName).toBe("CODE");
    expect(perm).toHaveClass("perm");
  });

  it("shows a saved status as one extra row under the changed row", async () => {
    vi.spyOn(api, "setSearchCadence").mockResolvedValue(item({ cadence_seconds: 900 }));
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Cadence for Phase A volume" }),
      "15 min",
    );
    expect(await screen.findByRole("status")).toHaveTextContent(/saved/i);
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });

  it("keeps each delay on one line", () => {
    render(<SearchCadenceTable items={[item()]} />);
    expect(screen.getByText("up to 1 h 7 min")).toHaveClass("nowrap");
  });

  it("makes no call when the same value is picked again", async () => {
    const put = vi.spyOn(api, "setSearchCadence");
    const user = userEvent.setup();
    render(<SearchCadenceTable items={[item()]} />);
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Cadence for Phase A volume" }),
      "1 h",
    );
    expect(put).not.toHaveBeenCalled();
  });
});
