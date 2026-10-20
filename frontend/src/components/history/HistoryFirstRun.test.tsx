import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, api, type CallerInfo, type HistoryConsole } from "@/lib/api";

import { HistoryFirstRun, SHOWN_KEY } from "./HistoryFirstRun";
import {
  LAB_ID,
  SECOND_ID,
  consoleOf,
  decision,
  estimateFamily,
  estimateOf,
} from "./fixtures";

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

function session(permissions: string[]): SessionValue {
  return {
    status: "ready",
    me: caller(permissions),
    error: null,
    reload: vi.fn(),
    signOut: vi.fn(),
  };
}

function mount(permissions: string[] = ["admin:history_backfill"]) {
  return render(
    <StaticSession value={session(permissions)}>
      <button type="button">page control</button>
      <HistoryFirstRun />
    </StaticSession>,
  );
}

function recorded(over: Partial<HistoryConsole> = {}): HistoryConsole {
  return consoleOf({
    decision: decision({ source: "UI", decided_by: "ada", answer: "YES" }),
    mode: "ACTIVE",
    ...over,
  });
}

beforeEach(() => {
  window.sessionStorage.clear();
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

afterEach(() => {
  window.sessionStorage.clear();
});

describe("the first-run dialog: who is asked", () => {
  it("does not ask a principal without admin:history_backfill, not even read:*", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    mount(["read:*", "read:history_backfill"]);
    await act(async () => {});
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("asks a principal holding admin:* once", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockResolvedValue([]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([]);
    mount(["admin:*"]);
    await waitFor(() => expect(read).toHaveBeenCalledTimes(1));
  });

  it("renders nothing, and no error, on a 403", async () => {
    vi.spyOn(api, "historyBackfill").mockRejectedValue(new ApiError(403, "forbidden"));
    mount();
    await act(async () => {});
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("is not shown when every console has decided", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([recorded()]);
    const estimate = vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    await act(async () => {});
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(estimate).not.toHaveBeenCalled();
  });

  it("is not shown again in the same browser session", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    const first = mount();
    await screen.findByRole("dialog");
    expect(window.sessionStorage.getItem(SHOWN_KEY)).toBe("1");
    first.unmount();
    read.mockClear();
    mount();
    await act(async () => {});
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("still opens when session storage cannot be read or written", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage blocked");
    });
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

describe("the first-run dialog: what it says", () => {
  beforeEach(() => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
  });

  it("asks the question with the days the estimate reports, never a fixed number", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([
      estimateOf({ families: [estimateFamily({ days_requested: 14 })] }),
    ]);
    mount();
    const dialog = await screen.findByRole("dialog", { name: "Read the last 14 days from QRadar?" });
    expect(within(dialog).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Read the last 14 days from QRadar?",
    );
  });

  it("lists each family with its state, and leaves Geography out with the reason", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    const table = within(dialog).getByRole("table");
    for (const name of ["Source volume", "WAF", "IPS", "Catch-all senders", "Rule matches"]) {
      expect(within(table).getByRole("rowheader", { name })).toBeInTheDocument();
    }
    const waf = within(table).getByRole("rowheader", { name: "WAF" }).closest("tr")!;
    expect(waf).toHaveTextContent("1 source enabled");
    expect(waf).toHaveTextContent("sources you enable later are read when you enable them");
    const ips = within(table).getByRole("rowheader", { name: "IPS" }).closest("tr")!;
    expect(ips).toHaveTextContent("Switched off. IPS_COLLECTION_ENABLED is off.");
    const geography = within(table).getByRole("rowheader", { name: "Geography" }).closest("tr")!;
    expect(geography).toHaveTextContent("Left out. No baseline to fill.");
  });

  it("states the estimate per family, says a reduced window is reduced, and labels it an estimate", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Yes" });
    const row = (name: string) =>
      within(dialog).getByRole("rowheader", { name }).closest("tr") as HTMLElement;
    expect(row("Source volume")).toHaveTextContent("5 searches, about 0.1 hours");
    expect(row("WAF")).toHaveTextContent("2,100 searches, about 72 hours");
    expect(row("WAF")).toHaveTextContent("Reduced: 20 of 28 days fit in 72 hours");
    expect(dialog).toHaveTextContent("These are estimates from a model, not measurements.");
    // Each family lists its own assumptions, and a duplicate is dropped only within a family.
    const wafList = within(dialog).getByRole("list", { name: "Assumptions for WAF" });
    expect(within(wafList).getAllByRole("listitem")).toHaveLength(2);
    expect(within(row("WAF")).getByText("QRadar time per search not measured on this console")).toBeInTheDocument();
    expect(within(dialog).getAllByText("5-minute buckets, 10 history searches per tick").length).toBeGreaterThan(1);
  });

  it("shows the reason for a null figure and never draws a zero", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Yes" });
    const ips = within(dialog).getByRole("rowheader", { name: "IPS" }).closest("tr") as HTMLElement;
    expect(ips).toHaveTextContent("no estimate: collection is switched off");
    expect(ips).not.toHaveTextContent(/\b0 searches\b/);
    expect(ips).not.toHaveTextContent(/about 0 hours/);
  });

  it("says why a refused window is refused", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([
      estimateOf({
        families: [
          estimateFamily({
            family: "WAF",
            refused: true,
            reduced: true,
            days_fitted: null,
            reason: "even 7 days do not fit in 72 hours",
          }),
        ],
      }),
    ]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Yes" });
    const waf = within(dialog).getByRole("rowheader", { name: "WAF" }).closest("tr") as HTMLElement;
    expect(waf).toHaveTextContent("Refused");
    expect(waf).toHaveTextContent("even 7 days do not fit in 72 hours");
  });

  it("states that nothing old is judged, the cost, and the property-watch line", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Yes" });
    expect(dialog).toHaveTextContent("Nothing old raises an alert.");
    expect(dialog).toHaveTextContent("Old data is used for learning only; it is never judged.");
    expect(dialog).toHaveTextContent(
      "This sends read-only searches to QRadar for about 72 hours; the schedule you set in Search cadence still applies.",
    );
    expect(dialog).toHaveTextContent("Property watches ask for history when you create the watch.");
    expect(dialog.textContent).not.toMatch(/live|real-time|!/i);
  });

  it("does not promise a duration when no family has a figure", async () => {
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([
      estimateOf({
        families: [estimateFamily({ searches: null, hours: null, ticks: null, reason: "no data yet" })],
      }),
    ]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Yes" });
    expect(dialog).toHaveTextContent("how long it takes is not estimated");
    expect(dialog.textContent).not.toMatch(/about 0 hours/);
  });

  it("shows the loading state while the estimate is read, with the answers disabled", async () => {
    let settle: (v: ReturnType<typeof estimateOf>[]) => void = () => {};
    vi.spyOn(api, "historyBackfillEstimate").mockReturnValue(
      new Promise((resolve) => {
        settle = resolve;
      }),
    );
    mount();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      "Loading the history backfill status",
    );
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(within(dialog).getByRole("button", { name: "Yes" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "No" })).toBeDisabled();
    expect(dialog).not.toHaveTextContent("No estimate was returned");
    await act(async () => settle([estimateOf()]));
    expect(within(dialog).getByRole("button", { name: "Yes" })).toBeEnabled();
    expect(dialog).toHaveAttribute("aria-busy", "false");
  });

  it("names what failed when the estimate cannot be read, without the exception", async () => {
    const estimate = vi
      .spyOn(api, "historyBackfillEstimate")
      .mockRejectedValueOnce(new Error("secret stack at /srv/app"))
      .mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("The estimate could not be loaded.");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(alert).toHaveTextContent("try again");
    expect(dialog).not.toHaveTextContent("secret stack");
    expect(within(dialog).getByRole("button", { name: "Yes" })).toBeDisabled();
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Yes" })).toBeEnabled());
    expect(estimate).toHaveBeenCalledTimes(2);
  });
});

describe("the first-run dialog: asking and answering", () => {
  it("puts the focus on the first control, gives No and Yes the same weight, and defaults to neither", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Yes" });
    const yes = within(dialog).getByRole("button", { name: "Yes" });
    const no = within(dialog).getByRole("button", { name: "No" });
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Decide later" }));
    expect(document.activeElement).not.toBe(yes);
    expect(yes.className).toBe(no.className);
    expect(yes.hasAttribute("autofocus")).toBe(false);
    // No comes first, then Yes, in the order the design names them.
    expect(yes.compareDocumentPosition(no) & Node.DOCUMENT_POSITION_PRECEDING).toBe(
      Node.DOCUMENT_POSITION_PRECEDING,
    );
  });

  it("closes on Escape without deciding, and returns the focus", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    const decide = vi.spyOn(api, "decideHistoryBackfill");
    mount();
    screen.getByRole("button", { name: "page control" }).focus();
    const dialog = await screen.findByRole("dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(decide).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "page control" }));
  });

  it("closes through Decide later without deciding", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    const decide = vi.spyOn(api, "decideHistoryBackfill");
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Decide later" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(decide).not.toHaveBeenCalled();
  });

  it.each([
    ["Yes", "YES"],
    ["No", "NO"],
  ] as const)("%s sends exactly that answer and closes", async (label, answer) => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    const decide = vi.spyOn(api, "decideHistoryBackfill").mockResolvedValue(recorded());
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: label }));
    expect(decide).toHaveBeenCalledWith(LAB_ID, answer);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("disables both answers and marks the row busy while saving", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    let settle: (v: HistoryConsole) => void = () => {};
    vi.spyOn(api, "decideHistoryBackfill").mockReturnValue(
      new Promise((resolve) => {
        settle = resolve;
      }),
    );
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Yes" }));
    expect(dialog).toHaveAttribute("aria-busy", "true");
    expect(within(dialog).getByRole("status")).toHaveTextContent("Saving");
    expect(within(dialog).getByRole("button", { name: "Yes" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "No" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Decide later" })).toBeDisabled();
    await act(async () => settle(recorded()));
  });

  it("shows what was recorded in the meantime when the answer is a 409", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    const winner = recorded({
      decision: decision({ answer: "NO", days: null, source: "UI", decided_by: "bea" }),
      mode: "NO",
    });
    vi.spyOn(api, "decideHistoryBackfill").mockRejectedValue(
      new ApiError(409, "a decision exists", "a decision exists", {
        detail: "a decision exists",
        console: winner,
      }),
    );
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Yes" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Your answer was not recorded.");
    expect(alert).toHaveTextContent("A decision was recorded in the meantime.");
    expect(alert).toHaveTextContent("History backfill: No, history is not read. Recorded by bea");
    // The dialog stays so the recorded answer can be read, and the answers are gone.
    expect(within(dialog).queryByRole("button", { name: "Yes" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("says nothing was changed on a failure, hides the exception and lets the analyst retry", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    const decide = vi
      .spyOn(api, "decideHistoryBackfill")
      .mockRejectedValueOnce(new ApiError(500, "psycopg2 exploded"))
      .mockResolvedValue(recorded());
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Yes" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Nothing was changed.");
    expect(alert).toHaveTextContent("try again");
    expect(dialog).not.toHaveTextContent("psycopg2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Yes" }));
    expect(decide).toHaveBeenCalledTimes(2);
  });

  it("names the permission when the answer is refused with a 403", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    vi.spyOn(api, "decideHistoryBackfill").mockRejectedValue(new ApiError(403, "forbidden"));
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "No" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("admin:history_backfill");
  });
});

describe("the first-run dialog: several consoles and a failed read", () => {
  it("asks once, with one decision row per undecided console and only the undecided ones", async () => {
    const second = consoleOf({ instance_id: SECOND_ID, name: "branch-console" });
    vi.spyOn(api, "historyBackfill").mockResolvedValue([
      consoleOf(),
      second,
      recorded({ instance_id: "fff", name: "decided-console" }),
    ]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([
      estimateOf(),
      estimateOf({ instance_id: SECOND_ID, name: "branch-console" }),
    ]);
    const decide = vi.spyOn(api, "decideHistoryBackfill").mockResolvedValue(recorded());
    mount();
    const dialog = await screen.findByRole("dialog");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    const group = within(dialog).getByRole("group", { name: "Your answer" });
    await within(group).findAllByRole("button", { name: "Yes" });
    expect(within(group).getAllByRole("button", { name: "Yes" })).toHaveLength(2);
    expect(dialog).toHaveTextContent("lab-console");
    expect(dialog).toHaveTextContent("branch-console");
    expect(dialog).not.toHaveTextContent("decided-console");
    // Deciding one console leaves the dialog open for the other.
    await userEvent.click(within(group).getAllByRole("button", { name: "No" })[0]!);
    expect(decide).toHaveBeenCalledWith(LAB_ID, "NO");
    await waitFor(() => expect(within(group).getAllByRole("button", { name: "Yes" })).toHaveLength(1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.click(within(group).getByRole("button", { name: "Yes" }));
    expect(decide).toHaveBeenCalledWith(SECOND_ID, "YES");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("says what failed when the status cannot be read, without the exception, and retries", async () => {
    const read = vi
      .spyOn(api, "historyBackfill")
      .mockRejectedValueOnce(new Error("boom at /srv/x"))
      .mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog");
    const alert = within(dialog).getByRole("alert");
    expect(alert).toHaveTextContent("Nothing was changed.");
    expect(alert).toHaveTextContent("try again");
    expect(dialog).not.toHaveTextContent("boom");
    expect(window.sessionStorage.getItem(SHOWN_KEY)).toBeNull();
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("dialog", { name: "Read the last 28 days from QRadar?" }),
    ).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });
});
