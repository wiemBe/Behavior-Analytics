import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, type CallerInfo, type FeatureOverview } from "@/lib/api";

import { FeaturesPage } from "./FeaturesPage";
import { NOW, overview, row, sw } from "./fixtures";

const api = vi.hoisted(() => ({ get: vi.fn(), set: vi.fn(), reset: vi.fn() }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  featuresApi: api,
}));

function session(permissions: string[]): SessionValue {
  const me = {
    subject: "ada",
    email: null,
    permissions,
    effective_permissions: permissions,
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: [],
  } as CallerInfo;
  return { status: "ready", me, error: null, reload: vi.fn(), signOut: vi.fn() };
}

async function open(data: FeatureOverview = overview(), permissions = ["admin:*"]) {
  api.get.mockResolvedValue(data);
  const view = render(
    <StaticSession value={session(permissions)}>
      <FeaturesPage />
    </StaticSession>,
  );
  await screen.findByRole("table");
  return view;
}

const rowOf = (name: string) => screen.getByRole("link", { name }).closest("tr") as HTMLElement;
const radio = (family: string, state: string) =>
  within(screen.getByRole("radiogroup", { name: `${family} state` })).getByRole("radio", { name: state });

beforeEach(() => {
  // Only the clock is faked: the ages and the stale rule read it, timers stay real.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  api.get.mockReset();
  api.set.mockReset().mockResolvedValue(undefined);
  api.reset.mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the table", () => {
  it("has one row per family, a caption and column headers, and links each family to its data", async () => {
    await open();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Family and prerequisites",
      "State and source",
      "Ariel load and cadence",
      "Needs from QRadar",
      "Last run",
    ]);
    expect(screen.getByRole("link", { name: "WAF" })).toHaveAttribute("href", "/behavior/waf");
    expect(screen.getByRole("link", { name: "Custom rules" })).toHaveAttribute("href", "/custom-rules");
    expect(screen.getAllByRole("radiogroup")).toHaveLength(4);
  });

  it("shows each family's state from its two switches", async () => {
    await open();
    expect(radio("WAF", "Shadow")).toBeChecked();
    expect(radio("IPS", "Alerting")).toBeChecked();
    expect(radio("Identity", "Shadow")).toBeChecked();
    // Alerting is stored but collection is off: Off, with the inactive note.
    expect(radio("Rule matches", "Off")).toBeChecked();
    expect(within(rowOf("Rule matches")).getByText("Alerting stored, inactive")).toBeInTheDocument();
    expect(within(rowOf("Log source volume")).getByText("Always on")).toBeInTheDocument();
  });

  it("names the source with a UTC time and the age after it, and offers the reset only for stored values", async () => {
    await open();
    const waf = rowOf("WAF");
    expect(within(waf).getByText(/Set by a.rivera/)).toBeInTheDocument();
    expect(waf).toHaveTextContent("11-05 17:05 UTC");
    expect(waf).toHaveTextContent("2 h ago");
    expect(within(waf).getByRole("button", { name: "Reset WAF to .env/default" })).toBeInTheDocument();
    expect(within(rowOf("Identity")).getByText("From .env")).toBeInTheDocument();
    expect(within(rowOf("Catch-all senders")).getByText("Default")).toBeInTheDocument();
    expect(within(rowOf("Identity")).queryByRole("button", { name: /Reset/ })).toBeNull();
  });

  it("shows the bound with its count beside the measured figure, and the cadence as a link", async () => {
    await open();
    const waf = rowOf("WAF");
    expect(waf).toHaveTextContent("up to 560/h");
    // Nothing is a column of its own any more: source, cadence and prerequisites live in these cells.
    expect(screen.getAllByRole("columnheader")).toHaveLength(5);
    expect(waf).toHaveTextContent("14 profiles · 531 last hour");
    expect(within(waf).getByRole("link", { name: "every 15 min" })).toHaveAttribute("href", "/admin/search-cadence");
    expect(waf).toHaveTextContent("14 of 16 enabled");
    expect(waf).toHaveTextContent("13 validated");
  });

  it("never draws a zero where nothing was measured", async () => {
    await open();
    expect(rowOf("Identity")).toHaveTextContent("last hour —");
    // The shared honesty labels, with the reason in the title.
    expect(within(rowOf("Identity")).getByText("Not collected", { selector: "[title='This family has not run.']" })).toBeInTheDocument();
    expect(rowOf("Catch-all senders")).toHaveTextContent("Insufficient data");
    expect(within(rowOf("Custom rules")).getByTitle("This family runs no scheduled Ariel search.")).toHaveTextContent("Not collected");
    expect(rowOf("Log source volume")).not.toHaveTextContent(/\b0\b/);
  });

  it("marks only a collecting family whose last run is older than two cadences as stale", async () => {
    await open();
    expect(within(rowOf("IPS")).getByText("Stale")).toBeInTheDocument();
    expect(screen.getAllByText("Stale")).toHaveLength(1);
  });

  it("says what the token reaches, advisory, with a link to the check", async () => {
    await open();
    const waf = rowOf("WAF");
    expect(within(waf).getByRole("link", { name: "Not reachable" })).toHaveAttribute("href", "/admin/qradar-access");
    expect(waf).toHaveTextContent("Custom event properties");
    expect(within(rowOf("Identity")).getByRole("link", { name: "Not checked yet" })).toBeInTheDocument();
    expect(within(rowOf("IPS")).getByText("Reachable")).toBeInTheDocument();
    expect(within(rowOf("Custom rules")).getByText("None")).toBeInTheDocument();
    // The not-reachable token does not block the control.
    expect(radio("WAF", "Alerting")).toBeEnabled();
  });

  it("says Admin only where the API sent no access evidence", async () => {
    const data = overview();
    data.families = data.families.map((f) => ({ ...f, access: null }));
    await open(data, ["read:features", "admin:features"]);
    expect(within(rowOf("WAF")).getByText("Needs admin:*")).toBeInTheDocument();
  });

  it("puts a settings problem on its row, and an unplaced one above the table", async () => {
    const data = overview({
      settings_problems: ["WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS (7 <= 14)", "ODD_SETTING is wrong"],
    });
    await open(data);
    expect(screen.getAllByText("Settings conflict")).toHaveLength(2);
    // On the row: a pill whose details hold the setting text; in the row, not in a row of its own.
    const note = within(rowOf("WAF")).getByText("WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS (7 <= 14)");
    expect(note.closest("details")).not.toBeNull();
    expect(screen.getByText("ODD_SETTING is wrong").closest("table")).toBeNull();
  });
});

describe("controls with a dependency", () => {
  it("disables a switch whose prerequisite is off and says what it needs", async () => {
    await open();
    expect(screen.getByRole("checkbox", { name: "Catch-all senders: New senders" })).toBeDisabled();
    expect(within(rowOf("Catch-all senders")).getByText("Needs collect on")).toBeInTheDocument();
    // The same switches are enabled while collection is on.
    expect(screen.getByRole("checkbox", { name: "WAF: Outcomes" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "Custom rules: Alert" })).toBeEnabled();
  });

  it("lets a collection switch on even while its alerting twin is blocked", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("checkbox", { name: "Catch-all senders: Collect" }));
    expect(api.set).toHaveBeenCalledWith("catchall_collection_enabled", true);
  });
});

describe("changing a state", () => {
  it("sends one write, then shows the server's answer, not the click", async () => {
    const user = userEvent.setup();
    await open();
    const after = overview();
    after.families[2] = row("identity", {
      ...after.families[2]!,
      switches: {
        identity_collection_enabled: sw(true, { source: "env" }),
        identity_alerting_enabled: sw(true, { source: "db", updated_by: "ada", updated_at: "2026-11-05T19:04:00Z" }),
      },
    });
    api.get.mockResolvedValue(after);
    await user.click(radio("Identity", "Alerting"));
    expect(api.set).toHaveBeenCalledTimes(1);
    expect(api.set).toHaveBeenCalledWith("identity_alerting_enabled", true);
    await waitFor(() => expect(radio("Identity", "Alerting")).toBeChecked());
    expect(screen.getByText("Saved.")).toBeInTheDocument();
    expect(within(rowOf("Identity")).getByText(/Set by ada/)).toBeInTheDocument();
  });

  it("turns collection on before alerting when going from Off", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(radio("Rule matches", "Alerting"));
    expect(api.set.mock.calls).toEqual([
      ["rule_match_collection_enabled", true],
      ["rule_alerting_enabled", true],
    ]);
  });

  it("does not change the radio until the server has answered", async () => {
    const user = userEvent.setup();
    await open();
    let release: () => void = () => {};
    api.set.mockReturnValue(new Promise<void>((resolve) => (release = resolve)));
    await user.click(radio("Identity", "Alerting"));
    expect(radio("Identity", "Shadow")).toBeChecked();
    expect(radio("Identity", "Alerting")).toBeDisabled();
    release();
    await screen.findByText("Saved.");
  });

  it("asks before turning off, sends nothing on Keep, and writes on the second click", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(radio("Identity", "Off"));
    const group = screen.getByRole("group", { name: "Turn off Identity?" });
    expect(api.set).not.toHaveBeenCalled();
    expect(within(group).getByRole("button", { name: "Keep" })).toHaveFocus();
    await user.click(within(group).getByRole("button", { name: "Keep" }));
    expect(screen.queryByRole("group", { name: "Turn off Identity?" })).toBeNull();
    expect(api.set).not.toHaveBeenCalled();
    await user.click(radio("Identity", "Off"));
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    expect(api.set).toHaveBeenCalledWith("identity_collection_enabled", false);
  });

  it("asks before leaving Alerting, and a plain toggle off asks too", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(radio("IPS", "Shadow"));
    expect(screen.getByText("Stop alerting on IPS?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Stop alerting" }));
    expect(api.set).toHaveBeenCalledWith("ips_alerting_enabled", false);
    await screen.findByText("Saved.");
    await user.click(screen.getByRole("checkbox", { name: "Custom rules: Evaluate" }));
    expect(screen.getByText("Turn off Custom rules evaluate?")).toBeInTheDocument();
    expect(api.set).toHaveBeenCalledTimes(1);
  });

  it("is operable from the keyboard", async () => {
    const user = userEvent.setup();
    await open();
    radio("Identity", "Alerting").focus();
    await user.keyboard(" ");
    expect(api.set).toHaveBeenCalledWith("identity_alerting_enabled", true);
  });
});

describe("refusals", () => {
  const refusal = (status: number, detail: unknown, message = "x") => new ApiError(status, message, detail);

  it("quotes a 422 as the API worded it and leaves the state alone", async () => {
    const user = userEvent.setup();
    await open();
    api.set.mockRejectedValue(
      refusal(422, { code: "feature_switch_dependency", problems: ["a", "b"], message: "waf_alerting_enabled; waf_collection_enabled" }),
    );
    await user.click(radio("Identity", "Alerting"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Not changed. The server refused it: waf_alerting_enabled; waf_collection_enabled");
    expect(radio("Identity", "Shadow")).toBeChecked();
    expect(api.get).toHaveBeenCalledTimes(1);
  });

  it("names the permission on a 403 and a rate limit on a 429", async () => {
    const user = userEvent.setup();
    await open();
    api.set.mockRejectedValueOnce(refusal(403, "missing required permission: admin:features"));
    await user.click(radio("Identity", "Alerting"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not changed. It needs the admin:features permission.");
    api.set.mockRejectedValueOnce(new ApiError(429, "slow", undefined, undefined, 12));
    await user.click(radio("Identity", "Alerting"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/12/));
  });

  it("says the status for other refusals and when the backend did not answer", async () => {
    const user = userEvent.setup();
    await open();
    api.set.mockRejectedValueOnce(refusal(409, { message: "feature_switch_database_disabled" }));
    await user.click(radio("Identity", "Alerting"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The server answered HTTP 409: feature_switch_database_disabled");
    api.set.mockRejectedValueOnce(new TypeError("network"));
    await user.click(radio("Identity", "Alerting"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("The backend did not answer."));
  });

  it("re-reads after a half-done change and says what was and was not turned on", async () => {
    const user = userEvent.setup();
    await open();
    api.set
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(refusal(422, { message: "RULE_MATCH_X must hold" }));
    await user.click(radio("Rule matches", "Alerting"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Collection was turned on, but alerting was not: RULE_MATCH_X must hold",
    );
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("says so when the read after a save fails", async () => {
    const user = userEvent.setup();
    await open();
    api.get.mockRejectedValue(new ApiError(500, "boom"));
    await user.click(radio("Identity", "Alerting"));
    expect(await screen.findByText(/Saved\. The page could not re-read/)).toBeInTheDocument();
    // The table the page already had stays on screen.
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});

describe("reset to .env/default", () => {
  it("asks first, then deletes the stored values, alerting first, and re-reads", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(screen.getByRole("button", { name: "Reset IPS to .env/default" }));
    expect(api.reset).not.toHaveBeenCalled();
    expect(screen.getByText("Reset IPS to .env/default?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(api.reset).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Reset IPS to .env/default" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(api.reset.mock.calls).toEqual([["ips_alerting_enabled"], ["ips_collection_enabled"]]);
    await screen.findByText("Saved.");
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("shows a refused reset in the server's words", async () => {
    const user = userEvent.setup();
    await open();
    api.reset.mockRejectedValue(new ApiError(422, "x", { message: "ips_alerting_enabled; ips_collection_enabled" }));
    await user.click(screen.getByRole("button", { name: "Reset IPS to .env/default" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The server refused it: ips_alerting_enabled; ips_collection_enabled");
  });
});

describe("reset that stops half-way, and focus", () => {
  it("says the reset stopped, not that collection was turned on", async () => {
    const user = userEvent.setup();
    await open();
    api.reset.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new ApiError(422, "x", { message: "ips_collection_enabled" }));
    await user.click(screen.getByRole("button", { name: "Reset IPS to .env/default" }));
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The reset stopped part-way: ips_collection_enabled");
    expect(api.get).toHaveBeenCalledTimes(2);
  });

  it("keeps the error when the re-read after a half-done change also fails", async () => {
    const user = userEvent.setup();
    await open();
    api.set.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new ApiError(422, "x", { message: "RULE_MATCH_X" }));
    api.get.mockRejectedValue(new ApiError(500, "boom"));
    await user.click(radio("Rule matches", "Alerting"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Collection was turned on, but alerting was not");
    expect(screen.queryByText(/Saved\./)).toBeNull();
  });

  it("returns focus to the control that started the change, also after Keep", async () => {
    const user = userEvent.setup();
    await open();
    await user.click(radio("Identity", "Off"));
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(radio("Identity", "Shadow")).toBeChecked();
    expect(document.activeElement).toBe(radio("Identity", "Off"));
    let release: () => void = () => {};
    api.set.mockReturnValue(new Promise<void>((resolve) => (release = resolve)));
    await user.click(radio("Identity", "Alerting"));
    // A browser drops focus from a control that becomes disabled: put it elsewhere.
    screen.getByRole("link", { name: "WAF" }).focus();
    expect(document.activeElement).not.toBe(radio("Identity", "Alerting"));
    release();
    await screen.findByText("Saved.");
    await waitFor(() => expect(document.activeElement).toBe(radio("Identity", "Alerting")));
  });
});

describe("permissions", () => {
  it("disables every control and hides the reset for a read-only account, naming the permission", async () => {
    await open(overview(), ["read:features"]);
    expect(screen.getByText(/Changing one needs/)).toHaveTextContent("admin:features");
    for (const control of [...screen.getAllByRole("radio"), ...screen.getAllByRole("checkbox")]) {
      expect(control).toBeDisabled();
    }
    expect(screen.queryByRole("button", { name: /Reset/ })).toBeNull();
    // Reading is still complete.
    expect(radio("IPS", "Alerting")).toBeChecked();
  });

  it("says the account cannot read when the API answers 403", async () => {
    api.get.mockRejectedValue(new ApiError(403, "forbidden"));
    render(
      <StaticSession value={session([])}>
        <FeaturesPage />
      </StaticSession>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Features cannot be shown to this account.");
    expect(alert).toHaveTextContent("read:features");
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("page states", () => {
  it("says it is loading", () => {
    api.get.mockReturnValue(new Promise(() => {}));
    render(
      <StaticSession value={session(["admin:*"])}>
        <FeaturesPage />
      </StaticSession>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading the feature switches");
  });

  it("says no console is enabled and still shows the switches", async () => {
    const data = overview();
    data.families = data.families.map((f) => ({ ...f, estimate: { ...f.estimate, enabled_consoles: 0, upper_bound_searches_per_hour: null, reason: "NOT_COLLECTED" }, access: [] }));
    await open(data);
    expect(screen.getByText("No QRadar console is enabled.")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(within(rowOf("WAF")).getByText("No console")).toBeInTheDocument();
    // No console, so no enabled consoles: not the profile count the family would otherwise show.
    expect(rowOf("WAF")).toHaveTextContent("No enabled consoles");
    expect(rowOf("WAF")).not.toHaveTextContent("No enabled profiles");
  });

  it("treats unreadable switches as off: a notice, no table, no states", async () => {
    api.get.mockResolvedValue(overview({ reason: "feature_switch_unreadable" }));
    render(
      <StaticSession value={session(["admin:*"])}>
        <FeaturesPage />
      </StaticSession>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The switches could not be read.");
    expect(alert).toHaveTextContent("Collectors treat every family as off");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("says what failed and offers a retry when the read fails", async () => {
    const user = userEvent.setup();
    api.get.mockRejectedValueOnce(new ApiError(500, "boom"));
    render(
      <StaticSession value={session(["admin:*"])}>
        <FeaturesPage />
      </StaticSession>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The backend answered with HTTP 500.");
    api.get.mockResolvedValue(overview());
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("says the backend did not answer when there was no response", async () => {
    api.get.mockRejectedValueOnce(new TypeError("network"));
    render(
      <StaticSession value={session(["admin:*"])}>
        <FeaturesPage />
      </StaticSession>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("The backend did not answer.");
  });

  it("uses no forbidden words", async () => {
    await open(overview({ settings_problems: ["PW_X must hold"] }));
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });
});
