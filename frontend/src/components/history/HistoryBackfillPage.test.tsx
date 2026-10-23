import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, api, type CallerInfo, type HistoryConsole } from "@/lib/api";

import { HistoryBackfillPage } from "./HistoryBackfillPage";
import { LAB_ID, consoleOf, decision, family, scope } from "./fixtures";

function caller(permissions: string[]): CallerInfo {
  return {
    subject: "ada",
    email: null,
    permissions,
    effective_permissions: permissions,
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["admin"],
  };
}

function view(permissions: string[] = ["admin:*"]) {
  const value: SessionValue = {
    status: "ready",
    me: caller(permissions),
    error: null,
    reload: vi.fn(),
    signOut: vi.fn(),
  };
  return render(
    <StaticSession value={value}>
      <HistoryBackfillPage />
    </StaticSession>,
  );
}

/** The console of the design's screenshot: Phase A reading, catch-all complete, WAF partial. */
function reading(): HistoryConsole {
  return consoleOf({
    decision: decision(),
    mode: "ACTIVE",
    families: [
      family({
        scopes: [
          scope({
            gaps: [
              {
                start: "2026-10-01T00:00:00Z",
                end: "2026-10-01T00:30:00Z",
                reason: "search failed 3 times at the minimum width",
              },
            ],
          }),
        ],
      }),
      family({
        family: "WAF",
        collection_reason: "WAF_COLLECTION_ENABLED is on",
        scopes: [
          scope({
            scope_key: "7",
            name: "web-7",
            later_yes: "not needed: this source has a history scope",
            state: "PARTIAL",
            floor_proved: true,
            floor: "2026-09-21T10:00:00Z",
            through: "2026-10-10T00:00:00Z",
            finished_at: "2026-10-10T01:00:00Z",
            reason: "the density gate stopped the reading",
            days_read: 19,
            buckets_read: 5472,
            searches: 640,
            gaps: [
              { start: "2026-10-02T03:00:00Z", end: "2026-10-02T04:00:00Z", reason: "search failed" },
            ],
          }),
        ],
        sources: [
          {
            scope_key: "9",
            name: "web-9",
            profile_enabled: true,
            later_yes: "not possible: this source already collected",
          },
        ],
        later_yes: null,
      }),
      family({
        family: "CATCHALL",
        scopes: [
          scope({
            state: "COMPLETE",
            finished_at: "2026-10-19T18:00:00Z",
            through: "2026-10-19T00:00:00Z",
            days_read: 28,
            buckets_read: 8064,
            searches: 90,
          }),
        ],
      }),
      family({
        family: "RULE_MATCH",
        collection_enabled: false,
        collection_reason: "RULE_MATCH_COLLECTION_ENABLED is off",
      }),
    ],
  });
}

function rowOf(name: string, scopeText?: string): HTMLElement {
  const rows = screen
    .getAllByRole("rowheader", { name })
    .map((h) => h.closest("tbody") as HTMLElement);
  const found = scopeText ? rows.find((r) => r.textContent?.includes(scopeText)) : rows[0];
  if (!found) throw new Error(`no ${name} row`);
  return found;
}

beforeEach(() => {
  vi.useRealTimers();
});

describe("the history backfill page: states", () => {
  it("says what it is loading", () => {
    vi.spyOn(api, "historyBackfill").mockReturnValue(new Promise(() => {}));
    view();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the history backfill status");
  });

  it("names the missing permission on a 403 and shows no table", async () => {
    vi.spyOn(api, "historyBackfill").mockRejectedValue(new ApiError(403, "forbidden"));
    view(["read:offenses"]);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("read:history_backfill");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says what failed and what to try on any other failure, without the exception", async () => {
    const read = vi
      .spyOn(api, "historyBackfill")
      .mockRejectedValueOnce(new Error("secret stack at /srv/app"))
      .mockResolvedValue([reading()]);
    view();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not be loaded");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(alert).toHaveTextContent(/try again/i);
    expect(alert).not.toHaveTextContent("secret stack");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("says that no console is enabled, and why that leaves nothing to show", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([]);
    view();
    const heading = await screen.findByText("No QRadar console is enabled.");
    const status = screen.getByRole("status");
    expect(status).toContainElement(heading);
    expect(status).toHaveTextContent("per enabled console");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("draws no chart", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const { container } = view();
    await screen.findByRole("table");
    expect(container.querySelector("canvas, svg, [role='img']")).toBeNull();
  });
});

describe("the history backfill page: the table", () => {
  beforeEach(() => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
  });

  it("has the design's columns and one row per family and scope", async () => {
    view();
    const table = await screen.findByRole("table", { name: /What has been read from QRadar/ });
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((h) => (h.firstChild?.textContent ?? "").trim());
    expect(headers).toEqual([
      "Family",
      "Scope",
      "State",
      "Window asked",
      "Window proved",
      "Read",
      "Searches",
      "Read through",
    ]);
    // Phase A, WAF scope, WAF source without a scope row, catch-all, rule matches.
    // Each family is a metric row plus a detail row for the reason and later-yes.
    expect(within(table).getAllByRole("row")).toHaveLength(1 + 10);
    expect(within(table).getAllByText("Reason")).toHaveLength(5);
    expect(within(table).getAllByText("Later Yes")).toHaveLength(5);
  });

  it("shows READING as days of the target, with the buckets beside it", async () => {
    view();
    await screen.findByRole("table");
    const row = rowOf("Source volume");
    expect(row).toHaveTextContent("Reading");
    expect(row).toHaveTextContent("9 of 28 days");
    expect(row).toHaveTextContent("58 buckets");
    expect(row).toHaveTextContent("12");
  });

  it("never lets 0.0 days read as nothing: the buckets are beside it", async () => {
    const c = reading();
    c.families[0]!.scopes = [scope({ days_read: 0, buckets_read: 3, searches: 1 })];
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const row = rowOf("Source volume");
    expect(row).toHaveTextContent("0.0 of 28 days");
    expect(row).toHaveTextContent("3 buckets");
  });

  it("draws no counts when the progress could not be read", async () => {
    const c = reading();
    c.families[0]!.scopes = [
      scope({ progress_unreadable: true, days_read: null, buckets_read: null, searches: null }),
    ];
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const row = rowOf("Source volume");
    expect(row).toHaveTextContent("progress could not be read");
    expect(row).not.toHaveTextContent(/buckets?\b/);
    expect(row).not.toHaveTextContent(/\bdays\b/);
  });

  it("shows a missing count as a dash, not a zero", async () => {
    const c = reading();
    c.families[0]!.scopes = [scope({ searches: null, buckets_read: null, days_read: null })];
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const cells = within(rowOf("Source volume")).getAllByRole("cell");
    // Read and Searches.
    expect(cells[4]).toHaveTextContent("—");
    expect(cells[5]).toHaveTextContent("—");
    expect(cells[5]).not.toHaveTextContent("0");
  });

  it("shows a floor only when it is the proved floor", async () => {
    view();
    await screen.findByRole("table");
    const unproved = rowOf("Source volume");
    expect(unproved).toHaveTextContent("per source");
    expect(unproved).not.toHaveTextContent("Window proved");
    // The scope's floor (2026-10-01 00:00 UTC) is where reading started: not drawn as a proved floor.
    const provedCell = within(unproved).getAllByRole("cell")[3]!;
    expect(provedCell).toHaveTextContent(/^per source$/);
    const proved = rowOf("WAF", "Log source 7");
    expect(proved).toHaveTextContent("Window proved");
    expect(proved).toHaveTextContent("09-21 10:00 UTC");
    expect(within(proved).getByText("09-21 10:00").closest("time")).toHaveAttribute(
      "title",
      "2026-09-21 10:00:00 UTC",
    );
  });

  it("states a partial reading's reason in words and its gaps as time ranges", async () => {
    view();
    await screen.findByRole("table");
    const row = rowOf("WAF", "Log source 7");
    expect(row).toHaveTextContent("Partial");
    expect(row).toHaveTextContent("the density gate stopped the reading");
    expect(row).toHaveTextContent("10-02 03:00 UTC to 10-02 04:00 UTC");
    expect(within(row).getByTitle("2026-10-02 03:00:00 UTC to 2026-10-02 04:00:00 UTC")).toHaveTextContent(
      "10-02 03:00 UTC to 10-02 04:00 UTC",
    );
    expect(row).toHaveTextContent("search failed");
  });

  it("caps the gaps it lists and says how many more there are", async () => {
    const c = reading();
    c.families[1]!.scopes[0]!.gaps = Array.from({ length: 5 }, (_, i) => ({
      start: `2026-10-0${i + 1}T00:00:00Z`,
      end: `2026-10-0${i + 1}T00:30:00Z`,
      reason: "search failed",
    }));
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const row = rowOf("WAF", "Log source 7");
    expect(within(row).getAllByRole("listitem")).toHaveLength(4);
    expect(row).toHaveTextContent("and 2 more");
  });

  it("shows UNAVAILABLE and NOT_ELIGIBLE as their own states with their reasons", async () => {
    const c = reading();
    c.families[0]!.scopes = [
      scope({
        state: "UNAVAILABLE",
        reason: "QRadar holds fewer than 7 days",
        days_read: null,
        buckets_read: null,
        searches: null,
        through: null,
      }),
    ];
    c.families[1]!.scopes = [
      scope({
        scope_key: "7",
        state: "NOT_ELIGIBLE",
        reason: "this source already collected live data",
        later_yes: "not possible: this source already collected",
        days_read: null,
        buckets_read: null,
        searches: null,
        through: null,
        finished_at: "2026-10-19T12:00:00Z",
      }),
    ];
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const unavailable = rowOf("Source volume");
    expect(unavailable).toHaveTextContent("Unavailable");
    expect(unavailable).toHaveTextContent("QRadar holds fewer than 7 days");
    const eligible = rowOf("WAF", "Log source 7");
    expect(eligible).toHaveTextContent("Not eligible");
    expect(eligible).toHaveTextContent("this source already collected live data");
    expect(eligible).toHaveTextContent("not possible: this source already collected");
  });

  it("lists a switched-off family as a row with its reason, not as an absence", async () => {
    view();
    await screen.findByRole("table");
    const row = rowOf("Rule matches");
    expect(row).toHaveTextContent("Switched off");
    expect(row).toHaveTextContent("RULE_MATCH_COLLECTION_ENABLED is off");
    expect(row).toHaveTextContent("possible");
  });

  it("lists a configured WAF source that has no scope row, with its Later Yes", async () => {
    view();
    await screen.findByRole("table");
    const row = rowOf("WAF", "web-9");
    expect(row).toHaveTextContent("Log source 9");
    expect(row).toHaveTextContent("not possible: this source already collected");
  });

  it("gives a console-wide family with no scope row the reason it has none", async () => {
    const c = reading();
    c.families[2]!.scopes = [];
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const row = rowOf("Catch-all senders");
    expect(row).toHaveTextContent("Not started");
    expect(row).toHaveTextContent("created at the family's next collection");
  });

  it("shows times with their zone, the relative age after them, and as time elements", async () => {
    view();
    await screen.findByRole("table");
    const row = rowOf("Catch-all senders");
    const time = row.querySelectorAll("time")[1];
    expect(time).toHaveAttribute("datetime", "2026-10-19T18:00:00Z");
    expect(time).toHaveTextContent("10-19 18:00 UTC");
    expect(time).toHaveAttribute("title", "2026-10-19 18:00:00 UTC");
    expect(row).toHaveTextContent(/ago/);
    expect(row).toHaveTextContent("finished 10-19 18:00 UTC");
    expect(row).toHaveTextContent("10-19 00:00 UTC");
  });

  it("labels the column for what it shows: the time read through, not a step time", async () => {
    view();
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("columnheader", { name: /Read through/ })).toBeInTheDocument();
    expect(within(table).queryByText(/Last step/)).not.toBeInTheDocument();
  });

  it("names a WAF or IPS scope row by its log source, and falls back to the id", async () => {
    const c = reading();
    c.families[1]!.scopes = [
      scope({ scope_key: "7", name: "web-7", state: "COMPLETE" }),
      scope({ scope_key: "8", state: "COMPLETE" }),
    ];
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    await screen.findByRole("table");
    const named = rowOf("WAF", "web-7");
    expect(named).toHaveTextContent("Log source 7");
    expect(rowOf("WAF", "Log source 8")).not.toHaveTextContent("web-");
  });

  it("right-aligns the numbers in tabular figures", async () => {
    view();
    await screen.findByRole("table");
    const row = rowOf("Source volume");
    const cells = within(row).getAllByRole("cell");
    expect(cells[2]).toHaveClass("num");
    expect(cells[4]).toHaveClass("num");
    expect(cells[5]).toHaveClass("num");
    expect(cells[6]).toHaveClass("num");
  });
});

describe("the history backfill page: the decision block", () => {
  it("states the answer, who, when and how", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    view();
    expect(await screen.findByText(/History backfill: Yes, read 28 days/)).toHaveTextContent(
      "History backfill: Yes, read 28 days. Recorded by the installer on 2026-10-19 12:00:00 UTC.",
    );
    expect(screen.getByText(/Mode: Yes: history is read\./)).toBeInTheDocument();
  });

  it("names a person who answered in the UI", async () => {
    const c = reading();
    c.decision = decision({ source: "UI", decided_by: "bea" });
    vi.spyOn(api, "historyBackfill").mockResolvedValue([c]);
    view();
    expect(await screen.findByText(/Recorded by bea on/)).toBeInTheDocument();
  });

  it("offers Read history now on a No, and records Yes after the plain-words confirmation", async () => {
    const no = consoleOf({
      decision: decision({ answer: "NO", days: null, source: "UI", decided_by: "bea" }),
      mode: "NO",
    });
    vi.spyOn(api, "historyBackfill").mockResolvedValue([no]);
    const decide = vi.spyOn(api, "decideHistoryBackfill").mockResolvedValue(reading());
    view();
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "Stop reading" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Read history now" }));
    const confirm = screen.getByRole("group", { name: "Read history now?" });
    expect(confirm).toHaveTextContent("This records Yes for 28 days.");
    expect(confirm).toHaveTextContent("Nothing old raises an alert.");
    expect(decide).not.toHaveBeenCalled();
    await userEvent.click(within(confirm).getByRole("button", { name: /Confirm/ }));
    expect(decide).toHaveBeenCalledWith(LAB_ID, "YES");
    expect(await screen.findByRole("status")).toHaveTextContent("Recorded. History backfill: Yes");
    expect(screen.getByRole("button", { name: "Stop reading" })).toBeInTheDocument();
  });

  it("cancels a confirmation without sending anything", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const stop = vi.spyOn(api, "stopHistoryBackfill");
    view();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    const confirm = screen.getByRole("group", { name: "Stop reading?" });
    expect(confirm).toHaveTextContent("Partial");
    expect(confirm).toHaveTextContent("Nothing is deleted.");
    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(stop).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Stop reading?" })).not.toBeInTheDocument();
  });

  it("stops reading and shows the response's message", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const message = "Stop reading is recorded. Each family ends its reading scopes at its next collection.";
    const stop = vi.spyOn(api, "stopHistoryBackfill").mockResolvedValue({
      ...reading(),
      decision: decision({
        stop_requested_at: "2026-10-20T08:00:00Z",
        stop_requested_by: "ada",
      }),
      mode: "STOPPED",
      message,
    });
    view();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(stop).toHaveBeenCalledWith(LAB_ID);
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByText(/Mode: Yes: reading was stopped\./)).toBeInTheDocument();
    expect(screen.getByText(/Stop reading was asked by ada on/)).toHaveTextContent(
      "Stop reading was asked by ada on 2026-10-20 08:00:00 UTC",
    );
    // Stopped: only Read history now is offered.
    expect(screen.queryByRole("button", { name: "Stop reading" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read history now" })).toBeInTheDocument();
  });

  it("starts reading from an active console with nothing reading", async () => {
    const idle = reading();
    for (const f of idle.families) for (const s of f.scopes) s.state = "COMPLETE";
    vi.spyOn(api, "historyBackfill").mockResolvedValue([idle]);
    const start = vi.spyOn(api, "startHistoryBackfill").mockResolvedValue({
      ...idle,
      message: "Read history now is recorded. Each family applies it at its next collection.",
    });
    view();
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "Stop reading" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Read history now" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(start).toHaveBeenCalledWith(LAB_ID);
    expect(await screen.findByText(/Read history now is recorded/)).toBeInTheDocument();
  });

  it("offers an undecided console both answers", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    view();
    await screen.findByRole("table");
    expect(screen.getByText(/History backfill: not decided/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record No" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read history now" })).toBeInTheDocument();
  });

  it("shows no action, and names the permission, without admin:history_backfill", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    view(["read:history_backfill"]);
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: "Stop reading" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Read history now" })).not.toBeInTheDocument();
    expect(screen.getByText("admin:history_backfill")).toBeInTheDocument();
  });

  it("shows what is recorded when a change meets a 409", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const now = { ...reading(), decision: decision({ stop_requested_at: "2026-10-20T08:00:00Z", stop_requested_by: "bea" }), mode: "STOPPED" as const };
    vi.spyOn(api, "stopHistoryBackfill").mockRejectedValue(
      new ApiError(409, "record Yes first", "record Yes first", { detail: "record Yes first", console: now }),
    );
    view();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not changed.");
    expect(alert).toHaveTextContent("record Yes first");
    expect(screen.getByText(/Stop reading was asked by bea/)).toBeInTheDocument();
  });

  it("says nothing was changed on a failure, without the exception", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    vi.spyOn(api, "stopHistoryBackfill").mockRejectedValue(new ApiError(500, "psycopg2 exploded"));
    view();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not changed.");
    expect(alert).not.toHaveTextContent("psycopg2");
    await waitFor(() => expect(screen.getByRole("button", { name: "Stop reading" })).toBeEnabled());
  });

  it("marks the block busy and disables the confirmation while it saves", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    let settle: (v: HistoryConsole) => void = () => {};
    vi.spyOn(api, "stopHistoryBackfill").mockReturnValue(new Promise((r) => (settle = r)));
    const { container } = view();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Stop reading" }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm/ }));
    expect(container.querySelector(".hb-decision")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    settle(reading());
  });
});
