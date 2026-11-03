import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { POLL_LIMIT_SECONDS, POLL_SECONDS } from "./access";
import { access, answer, consoleOf, entry, REFUSED } from "./fixtures";
import { QRadarAccessPage } from "./QRadarAccessPage";

const api = vi.hoisted(() => ({ get: vi.fn(), check: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  qradarAccessApi: api,
}));

beforeEach(() => {
  api.get.mockReset();
  api.check.mockReset();
  api.get.mockResolvedValue(access());
});
afterEach(() => {
  vi.useRealTimers();
});

const running = (over = {}) => access({ consoles: [consoleOf({ check_running: true, ...over })] });

async function open() {
  const view = render(<QRadarAccessPage />);
  await screen.findByRole("heading", { name: "qradar-lab", level: 2 });
  return view;
}

async function openFake(data = access()) {
  vi.useFakeTimers({ shouldAdvanceTime: false });
  api.get.mockResolvedValue(data);
  const view = render(<QRadarAccessPage />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  return view;
}

const tick = (seconds: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(seconds * 1000);
  });

describe("QRadar access page: the first screen", () => {
  it("says every API is reachable, with the last check in UTC and the interval in words", async () => {
    await open();
    expect(screen.getByText("Every API the application uses is reachable with this token.")).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "qradar-lab" });
    expect(within(section).getByText(/Last checked/)).toHaveTextContent("2026-11-03 09:15:00 UTC");
    expect(within(section).getByText(/Scheduled checks run every 6 h\./)).toBeInTheDocument();
  });

  it("names the refused APIs and counts them", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    expect(
      screen.getByText(
        "4 APIs are refused: Offenses, Custom event properties, Building blocks, License pool.",
      ),
    ).toBeInTheDocument();
  });

  it("says the token itself is not accepted when an entry answered 401", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({
            entries: [entry({ status: "unauthorized", http_status: 401 }), entry({ id: "b", title: "B" })],
          }),
        ],
      }),
    );
    await open();
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("QRadar does not accept the token itself (HTTP 401).");
    expect(banner).toHaveClass("crit");
    expect(screen.getByText("Token not accepted")).toHaveClass("crit");
  });

  it("lists refused entries first in the table", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    const rows = within(screen.getByRole("table", { name: "QRadar APIs checked on qradar-lab" }))
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[0].querySelector(".qa-title")?.textContent);
    expect(rows).toEqual([
      "Offenses",
      "Custom event properties",
      "Building blocks",
      "License pool",
      "Ariel searches",
      "System information",
      "Log sources",
    ]);
  });

  it("shows status words with tones, the method and path in mono, the code, and what changed", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    const row = screen.getByText("Offenses").closest("tr")!;
    expect(within(row).getByText("Forbidden")).toHaveClass("warn");
    expect(within(row).getByText("was reachable")).toBeInTheDocument();
    expect(within(row).getByText("GET /api/siem/offenses").tagName).toBe("CODE");
    expect(within(row).getByText("403")).toBeInTheDocument();
    expect(within(row).getByText("The offense snapshot.")).toBeInTheDocument();
    expect(within(row).getByText("Without it: offense snapshot collector.")).toBeInTheDocument();
    const notFound = screen.getByText("License pool").closest("tr")!;
    expect(within(notFound).getByText("Not found")).toHaveClass("warn");
    const silent = screen.getByText("Ariel searches").closest("tr")!;
    expect(within(silent).getByText("Did not answer")).toHaveClass("warn");
    // No code reached: a dash, never a zero.
    expect(within(silent).getByText("—", { selector: ".num span" })).toBeInTheDocument();
  });

  it("never shows a zero or a blank for an entry that was never checked", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({
            last_checked_at: null,
            last_check_finished_at: null,
            entries: [entry({ status: null, http_status: null, checked_at: null })],
          }),
        ],
      }),
    );
    await open();
    expect(screen.getByText(/Not checked yet\. The first check runs at start-up/)).toBeInTheDocument();
    expect(screen.getByText("No check has finished on this console yet.", { exact: false })).toBeInTheDocument();
    const row = screen.getByText("Log sources").closest("tr")!;
    expect(within(row).getByText("Not checked yet")).toBeInTheDocument();
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    expect(within(row).queryByText("Reachable")).not.toBeInTheDocument();
  });
});

describe("QRadar access page: what to ask for", () => {
  it("words capability_hint as a hint, never as the required capability", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    const row = screen.getByText("Offenses").closest("tr")!;
    expect(
      within(row).getByText(
        "IBM's role table suggests: Offenses. This is a hint. Confirm it with your QRadar administrator.",
      ),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("Required capability");
  });

  it("shows required_capability plainly only for a verified entry", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({
            entries: [
              entry({
                status: "forbidden",
                http_status: 403,
                required_capability: "Offenses (View)",
                capability_verified: true,
                capability_hint: "a different hint",
              }),
            ],
          }),
        ],
      }),
    );
    await open();
    expect(screen.getByText("Required capability: Offenses (View). IBM states this for the API.")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("suggests");
  });

  it("does not present an unverified required_capability as fact", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({
            entries: [
              entry({
                status: "forbidden",
                http_status: 403,
                required_capability: "Guess",
                capability_verified: false,
              }),
            ],
          }),
        ],
      }),
    );
    await open();
    expect(document.body).not.toHaveTextContent("Guess");
    expect(screen.getByText(/IBM's role table suggests: Read-only Configuration/)).toBeInTheDocument();
  });

  it("says IBM documents nothing when an entry has no hint, and explains a 404", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    const blocks = within(screen.getByText("Building blocks").closest("tr")!);
    expect(blocks.getByText(/IBM documents no capability for this API/)).toBeInTheDocument();
    const pool = within(screen.getByText("License pool").closest("tr")!);
    expect(pool.getByText(/QRadar answered 404/)).toBeInTheDocument();
  });

  it("says the action once in the verdict box on a 401, and gives no row an ask block", async () => {
    const rows = Array.from({ length: 5 }, (_, i) =>
      entry({ id: `e${i}`, title: `API ${i}`, status: "unauthorized", http_status: 401 }),
    );
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: rows })] }));
    await open();
    const sentence = /The token itself was refused\. Replace or re-issue it, then press Check now\./;
    expect(screen.getAllByText(sentence)).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Replace or re-issue it");
    const table = screen.getByRole("table", { name: "QRadar APIs checked on qradar-lab" });
    expect(within(table).queryByText("What to ask for")).not.toBeInTheDocument();
    expect(within(table).getAllByText("Token not accepted")).toHaveLength(5);
  });

  it("says an identical ask once when every refused row would carry it", async () => {
    const rows = [1, 2, 3].map((i) =>
      entry({ id: `h${i}`, title: `API ${i}`, status: "forbidden", http_status: 403, capability_hint: "Log Activity" }),
    );
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: rows })] }));
    await open();
    expect(screen.getAllByText(/IBM's role table suggests: Log Activity\./)).toHaveLength(1);
    expect(screen.getAllByText("What to ask for")).toHaveLength(1);
  });

  it("gives a 404 only its own sentence, never a hint or a capability", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({
            entries: [
              entry({
                status: "not_found",
                http_status: 404,
                capability_hint: "Admin",
                required_capability: "Admin (verified)",
                capability_verified: true,
              }),
            ],
          }),
        ],
      }),
    );
    await open();
    expect(screen.getByText(/QRadar answered 404/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("suggests");
    expect(document.body).not.toHaveTextContent("Required capability");
    expect(document.body).not.toHaveTextContent("Admin");
  });

  it("lets a path break only after a slash: a wbr follows each slash and no segment is split", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    const call = screen
      .getAllByText(/regex_properties/, { selector: "code" })
      .find((el) => el.textContent === "GET /api/config/event_sources/custom_properties/regex_properties")!;
    expect(call.querySelectorAll("wbr")).toHaveLength(5);
    expect(call.textContent).toBe(
      "GET /api/config/event_sources/custom_properties/regex_properties",
    );
    for (const wbr of call.querySelectorAll("wbr")) {
      expect(wbr.previousSibling?.textContent).toBe("/");
    }
  });

  it("shows no ask block for a reachable entry", async () => {
    await open();
    expect(screen.queryByText("What to ask for")).not.toBeInTheDocument();
  });

  it("links the IBM sources outward with rel=noreferrer and lists covered calls with their reason", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    const row = screen.getByText("Custom event properties").closest("tr")!;
    const details = row.querySelector("details")!;
    await userEvent.click(within(row).getByText("Covered calls and sources"));
    expect(details.open).toBe(true);
    expect(within(row).getByText("Reads the same collection by id.")).toBeInTheDocument();
    const link = within(row).getByRole("link", { name: /ibm\.com/ });
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });
});

describe("QRadar access page: Check now", () => {
  it("posts, shows started, polls while running and ends with the syncs it started", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.check.mockResolvedValue(answer({ ...running(), started: ["qradar-lab"] }));
    render(<QRadarAccessPage />);
    await screen.findByRole("button", { name: "Check now" });
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("Check started on qradar-lab.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled();
    expect(screen.getByText(/A check is running\. This page reads the result every 5 s\./)).toBeInTheDocument();

    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({ syncs_started: ["log_source_inventory", "rule_inventory"] }),
        ],
      }),
    );
    await tick(POLL_SECONDS);
    expect(screen.getByText("The check ended.")).toBeInTheDocument();
    expect(
      screen.getByText("Started: log source inventory, rule inventory."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
    const reads = api.get.mock.calls.length;
    await tick(POLL_SECONDS * 3);
    expect(api.get.mock.calls.length).toBe(reads);
  });

  it("says a finished check started no syncs when it started none", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.check.mockResolvedValue(answer({ ...running(), started: ["qradar-lab"] }));
    render(<QRadarAccessPage />);
    await user.click(await screen.findByRole("button", { name: "Check now" }));
    await screen.findByText("Check started on qradar-lab.");
    await tick(POLL_SECONDS);
    expect(screen.getByText(/It started no syncs\./)).toBeInTheDocument();
  });

  it("names a console that already had a check running and queues nothing for it", async () => {
    const user = userEvent.setup();
    api.check.mockResolvedValue(answer({ ...running(), already_running: ["qradar-lab"] }));
    await open();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(
      await screen.findByText("A check was already running on qradar-lab. Nothing was queued for it."),
    ).toBeInTheDocument();
  });

  it("names a console whose check the queue refused", async () => {
    const user = userEvent.setup();
    api.check.mockResolvedValue(answer({ failed: ["qradar-lab"] }));
    await open();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(
      await screen.findByText("The queue refused the check for qradar-lab. Nothing ran there. Try again in a minute."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });

  it("words a 429 with the time to retry", async () => {
    const user = userEvent.setup();
    api.check.mockRejectedValue(new ApiError(429, "rate", "rate", null, 42));
    await open();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("Too many checks were requested. Try again in 42 s.")).toBeInTheDocument();
  });

  it("words a 429 that sent no retry time", async () => {
    const user = userEvent.setup();
    api.check.mockRejectedValue(new ApiError(429, "rate", "rate", null, null));
    await open();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(await screen.findByText("Too many checks were requested. Try again in a minute.")).toBeInTheDocument();
  });

  it("names the permission when the POST is forbidden, and says nothing was started", async () => {
    const user = userEvent.setup();
    api.check.mockRejectedValue(new ApiError(403, "no", "no"));
    await open();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    const text = await screen.findByText(/Your role cannot start a check/);
    expect(text).toHaveTextContent("admin:*");
    expect(text).toHaveTextContent("Nothing was started.");
  });

  it("says what failed for any other error on the POST", async () => {
    const user = userEvent.setup();
    api.check.mockRejectedValue(new ApiError(500, "boom", "boom"));
    await open();
    await user.click(screen.getByRole("button", { name: "Check now" }));
    expect(
      await screen.findByText("The check could not be requested (HTTP 500). Nothing ran. Try again."),
    ).toBeInTheDocument();
  });

  it("polls a check that was already running when the page opened, and keeps Check now off", async () => {
    await openFake(running());
    expect(screen.getByRole("button", { name: "Check now" })).toBeDisabled();
    expect(screen.getByText("Check running")).toBeInTheDocument();
    api.get.mockClear();
    api.get.mockResolvedValue(access());
    await tick(POLL_SECONDS);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Check now" })).toBeEnabled();
  });
});

describe("QRadar access page: polling bounds", () => {
  it("stops polling when the page unmounts", async () => {
    const { unmount } = await openFake(running());
    api.get.mockClear();
    await tick(POLL_SECONDS);
    expect(api.get).toHaveBeenCalledTimes(1);
    unmount();
    await tick(POLL_SECONDS * 5);
    expect(api.get).toHaveBeenCalledTimes(1);
  });

  it("stops after the bound and says the check is still running in the worker", async () => {
    await openFake(running());
    api.get.mockClear();
    await tick(POLL_LIMIT_SECONDS + POLL_SECONDS * 2);
    expect(api.get.mock.calls.length).toBe(POLL_LIMIT_SECONDS / POLL_SECONDS);
    expect(
      screen.getByText(/The check is still running in the worker\. This page stopped reading after 3 min\./),
    ).toBeInTheDocument();
    const reads = api.get.mock.calls.length;
    await tick(POLL_SECONDS * 10);
    expect(api.get.mock.calls.length).toBe(reads);
  });

  it("does not read while the tab is hidden", async () => {
    await openFake(running());
    api.get.mockClear();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    await tick(POLL_SECONDS * 4);
    expect(api.get).not.toHaveBeenCalled();
    hidden.mockReturnValue(false);
    await tick(POLL_SECONDS);
    expect(api.get).toHaveBeenCalledTimes(1);
    hidden.mockRestore();
  });

  it("does not poll when no check runs", async () => {
    await openFake(access());
    api.get.mockClear();
    await tick(POLL_SECONDS * 6);
    expect(api.get).not.toHaveBeenCalled();
  });

  it("keeps the last answer on screen and says so when a refresh fails", async () => {
    await openFake(running());
    api.get.mockRejectedValue(new ApiError(502, "bad", "bad"));
    await tick(POLL_SECONDS);
    expect(screen.getByText(/The last refresh failed\./)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "qradar-lab", level: 2 })).toBeInTheDocument();
  });
});

describe("QRadar access page: other consoles and states", () => {
  it("says a console that is not on the direct REST provider is not checked", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf({ applicable: false, entries: [], last_checked_at: null }),
          consoleOf({
            instance_id: "22222222-2222-4222-8222-222222222222",
            instance_name: "qradar-prod",
          }),
        ],
      }),
    );
    render(<QRadarAccessPage />);
    await screen.findByRole("heading", { name: "qradar-prod", level: 2 });
    const lab = screen.getByRole("region", { name: "qradar-lab" });
    expect(lab).toHaveTextContent("This console is not on the direct REST provider. Nothing is checked there.");
    expect(within(lab).queryByRole("table")).not.toBeInTheDocument();
    expect(within(lab).queryByText(/Not checked yet/)).not.toBeInTheDocument();
  });

  it("gives each console its own verdict", async () => {
    api.get.mockResolvedValue(
      access({
        consoles: [
          consoleOf(),
          consoleOf({
            instance_id: "22222222-2222-4222-8222-222222222222",
            instance_name: "qradar-prod",
            entries: REFUSED,
          }),
        ],
      }),
    );
    render(<QRadarAccessPage />);
    await screen.findByRole("heading", { name: "qradar-prod", level: 2 });
    expect(within(screen.getByRole("region", { name: "qradar-lab" })).getByText(/Every API/)).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "qradar-prod" })).getByText(/4 APIs are refused/)).toBeInTheDocument();
  });

  it("says no console is enabled, and offers no Check now", async () => {
    api.get.mockResolvedValue(access({ consoles: [] }));
    render(<QRadarAccessPage />);
    expect(await screen.findByText("No QRadar console is enabled.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
  });

  it("shows the loading state in words", () => {
    api.get.mockReturnValue(new Promise(() => {}));
    render(<QRadarAccessPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the QRadar access results");
  });

  it("names admin:* when the page's own read is forbidden", async () => {
    api.get.mockRejectedValue(new ApiError(403, "no", "no"));
    render(<QRadarAccessPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("QRadar access cannot be shown to this account.");
    expect(alert).toHaveTextContent("admin:*");
    expect(screen.queryByRole("button", { name: "Check now" })).not.toBeInTheDocument();
  });

  it("says what failed and offers to try again when the read fails", async () => {
    const user = userEvent.setup();
    api.get.mockRejectedValueOnce(new ApiError(500, "boom", "boom"));
    render(<QRadarAccessPage />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not read the QRadar access results.");
    expect(alert).toHaveTextContent("The backend answered with HTTP 500.");
    expect(alert).toHaveTextContent("nothing was sent to QRadar");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "qradar-lab", level: 2 })).toBeInTheDocument();
  });

  it("says the backend did not answer when there was no response", async () => {
    api.get.mockRejectedValueOnce(new TypeError("network"));
    render(<QRadarAccessPage />);
    expect(await screen.findByText("The backend did not answer.")).toBeInTheDocument();
  });

  it("holds no forbidden words: live, real-time, exclamation marks", async () => {
    api.get.mockResolvedValue(access({ consoles: [consoleOf({ entries: REFUSED })] }));
    await open();
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });
});
