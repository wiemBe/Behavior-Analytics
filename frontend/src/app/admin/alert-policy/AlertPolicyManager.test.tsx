import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, alertPolicyApi, api, fleetApi, type CallerInfo } from "@/lib/api";

import { AlertPolicyManager } from "./AlertPolicyManager";
import { INST_A, MIXED, SRC_FW, WATCH, digest, fleetSource, policy, watch } from "./fixtures";

function session(permissions: string[], status: SessionValue["status"] = "ready"): SessionValue {
  const me = { subject: "a", email: null, permissions, effective_permissions: permissions } as CallerInfo;
  return {
    status,
    me: status === "ready" ? me : null,
    error: null,
    reload: async () => {},
    signOut: async () => null,
  };
}

const ADMIN = session(["read:alerts", "admin:outage"]);
const READER = session(["read:alerts"]);

function mount(rows = MIXED, value: SessionValue = ADMIN, d = digest()) {
  vi.spyOn(alertPolicyApi, "list").mockResolvedValue(rows);
  vi.spyOn(alertPolicyApi, "digest").mockResolvedValue(d);
  return render(
    <StaticSession value={value}>
      <AlertPolicyManager
        initialRows={rows}
        initialDigest={d}
        digestNote={null}
        initialQuiet={[]}
        quietFailure={null}
      />
    </StaticSession>,
  );
}

const bodyRows = () => within(screen.getByRole("table", { name: /^Alert policy rows/ })).getAllByRole("row");

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the table", () => {
  it("lists SOURCE rows before CLASS rows, each with family, detector, mode, editor and UTC time", () => {
    mount();
    const rows = bodyRows().slice(1);
    const dataRows = rows.filter((r) => within(r).queryAllByRole("rowheader").length > 0);
    const names = dataRows.map((r) => within(r).getAllByRole("rowheader")[0].textContent);
    expect(names[0]).toMatch(/^admin-logins/);
    expect(names[1]).toMatch(/^edge-waf/);
    expect(names[2]).toMatch(/^pa-fw-01/);
    expect(names[3]).toMatch(/^Firewall/);
    const waf = dataRows[1];
    expect(within(waf).getAllByText("WAF")).toHaveLength(2);
    expect(within(waf).getByText("Request-rate spike")).toBeInTheDocument();
    expect(within(waf).getByText("Digest")).toBeInTheDocument();
    expect(within(waf).getByText("Console lab-console")).toBeInTheDocument();
    expect(within(waf).getByText("analyst")).toBeInTheDocument();
    expect(waf.textContent).toMatch(/UTC/);
    const fw = dataRows[3];
    expect(within(fw).getByText("All detectors")).toBeInTheDocument();
    expect(within(fw).getByText("All consoles")).toBeInTheDocument();
    expect(within(fw).getByText("Record only")).toBeInTheDocument();
  });

  it("names a property-watch row by its watch and its detector by the watch vocabulary", () => {
    mount();
    expect(screen.getByText("admin-logins")).toBeInTheDocument();
    expect(screen.getByText("New value")).toBeInTheDocument();
    expect(screen.getByText("Property watch")).toBeInTheDocument();
  });

  it("a NO_EVENTS row, and an all-detectors log-source row, say the outage policy wins, with a link", () => {
    mount();
    const notes = screen.getAllByText(/A record-only outage policy wins; this policy can only digest or alert/);
    // pa-fw-01 NO_EVENTS and the Firewall class row (all detectors of log_source).
    expect(notes).toHaveLength(2);
    expect(within(notes[0]).getByRole("link", { name: "Outage alerts" })).toHaveAttribute(
      "href",
      "/admin/outage-policy",
    );
  });

  it("renders the three modes as neutral pills, never a status hue", () => {
    mount();
    const pills = ["Alert", "Record only", "Digest"].flatMap((m) =>
      screen.queryAllByText(m, { selector: "td .pill" }),
    );
    expect(pills.length).toBeGreaterThan(0);
    for (const el of pills) expect(el.className).toBe("pill");
  });

  it("no other row carries the outage note", () => {
    mount([MIXED[1], MIXED[3]]);
    expect(screen.queryByText(/outage policy wins/)).toBeNull();
  });

  it("a source that left the inventory is named as gone, with its id, and cannot be edited", () => {
    mount([policy({ scope_kind: "SOURCE", scope_value: SRC_FW, source_name: null, source_class: null, detector_type: "VOLUME_DROP" })]);
    expect(screen.getByText("Source no longer in inventory")).toBeInTheDocument();
    expect(screen.getByText("id 33333333")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.getByRole("button", { name: /^Remove the policy for / })).toBeInTheDocument();
  });

  it("explains the modes once and the default, the family switch and the precedence", () => {
    mount();
    expect(screen.getAllByText("raises an alert, as before.")).toHaveLength(1);
    expect(screen.getByText("records the incident and raises no alert.")).toBeInTheDocument();
    expect(screen.getByText("records the incident and lists it in the daily e-mail.")).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/A family whose alerting switch is off raises nothing, whatever its policy\./);
  });
});

describe("empty and read-only", () => {
  it("empty: says every source alerts as before and offers Add policy", () => {
    mount([]);
    expect(screen.getByRole("status")).toHaveTextContent("Every source alerts as before (default ALERT).");
    expect(screen.queryByRole("table", { name: /^Alert policy rows/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Add policy" })).toBeInTheDocument();
  });

  it("without admin:outage the table is read-only and names the permission", () => {
    mount(MIXED, READER);
    expect(screen.getByText(/needs the admin:outage permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add policy" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Actions" })).toBeNull();
    expect(screen.getByText("edge-waf")).toBeInTheDocument();
  });

  it("while the session loads it makes no claim either way", () => {
    mount(MIXED, session([], "loading"));
    expect(screen.queryByText(/needs the admin:outage permission/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Add policy" })).toBeNull();
    expect(screen.getByText("edge-waf")).toBeInTheDocument();
  });

  it("the administrator wildcard may edit", () => {
    mount(MIXED, session(["read:alerts", "admin:*"]));
    expect(screen.getByRole("button", { name: "Add policy" })).toBeInTheDocument();
  });
});

describe("editing a row", () => {
  it("keeps the identity fixed and saves only a new mode, then reloads the list and says what changed", async () => {
    const user = userEvent.setup();
    mount();
    const put = vi.spyOn(alertPolicyApi, "put").mockResolvedValue(MIXED[1]);
    await user.click(screen.getByRole("button", { name: "Edit the policy for edge-waf, Request-rate spike" }));
    const form = screen.getByRole("form", { name: "Edit the policy for edge-waf" });
    expect(within(form).queryByLabelText("Family")).toBeNull();
    expect(within(form).getByRole("button", { name: "Save policy" })).toBeDisabled();
    expect(form).toHaveTextContent("Request-rate spike");
    await user.click(within(form).getByRole("radio", { name: "Alert" }));
    expect(within(form).getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "edge-waf: Request-rate spike changes from Digest to Alert.",
    );
    await user.click(within(form).getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved: edge-waf, Request-rate spike, now Alert."));
    expect(put).toHaveBeenCalledWith({
      instance_id: INST_A,
      scope_kind: "SOURCE",
      scope_value: MIXED[1].scope_value,
      family: "waf",
      detector_type: "WAF_REQUEST_RATE_SPIKE",
      mode: "ALERT",
    });
    expect(alertPolicyApi.list).toHaveBeenCalled();
    expect(screen.queryByRole("form")).toBeNull();
  });

  it("a refused save keeps the editor and says nothing changed when forbidden", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(alertPolicyApi, "put").mockRejectedValue(new ApiError(403, "no"));
    await user.click(screen.getByRole("button", { name: /^Edit the policy for edge-waf/ }));
    await user.click(screen.getByRole("radio", { name: "Alert" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/admin:outage permission.*Nothing was saved/);
    expect(screen.getByRole("form")).toBeInTheDocument();
  });
});

describe("adding a policy", () => {
  it("opens the same editor, empty: Save is off until something is governed", async () => {
    const user = userEvent.setup();
    mount([]);
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    const form = screen.getByRole("form", { name: "Add a policy" });
    expect(within(form).getByRole("button", { name: "Save policy" })).toBeDisabled();
    expect(within(form).getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "Choose what the policy governs first.",
    );
    expect(screen.queryByRole("button", { name: "Add policy" })).toBeNull();
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.getByRole("button", { name: "Add policy" })).toBeInTheDocument();
  });

  it("a class policy for one detector: offers the family's own detectors and sends the exact body", async () => {
    const user = userEvent.setup();
    mount([]);
    const put = vi.spyOn(alertPolicyApi, "put").mockResolvedValue(policy());
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.click(screen.getByRole("radio", { name: "A whole class" }));
    await user.selectOptions(screen.getByLabelText("Class"), "FIREWALL");
    const detectors = within(screen.getByLabelText("Detector")).getAllByRole("option").map((o) => o.textContent);
    expect(detectors[0]).toBe("All detectors");
    expect(detectors).toHaveLength(10);
    expect(detectors).toContain("No events");
    expect(detectors).not.toContain("Request-rate spike");
    await user.selectOptions(screen.getByLabelText("Detector"), "VOLUME_SPIKE");
    await user.click(screen.getByRole("radio", { name: "Digest" }));
    expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "Firewall: Volume spike becomes Digest.",
    );
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(put).toHaveBeenCalledWith({
      instance_id: null,
      scope_kind: "CLASS",
      scope_value: "FIREWALL",
      family: "log_source",
      detector_type: "VOLUME_SPIKE",
      mode: "DIGEST",
    });
    expect(await screen.findByText("Saved: Firewall, Volume spike, now Digest.")).toBeInTheDocument();
  });

  it("changing the family resets the detector to the new family's list", async () => {
    const user = userEvent.setup();
    mount([]);
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.selectOptions(screen.getByLabelText("Detector"), "VOLUME_SPIKE");
    await user.selectOptions(screen.getByLabelText("Family"), "waf");
    expect(screen.getByLabelText("Detector")).toHaveValue("");
    const options = within(screen.getByLabelText("Detector")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toContain("Request-rate spike");
    expect(options).not.toContain("Volume spike");
  });

  it("a source policy: chooses from the searched inventory and pins the source's console", async () => {
    const user = userEvent.setup();
    mount([]);
    vi.spyOn(fleetApi, "sources").mockResolvedValue({ items: [fleetSource()], total: 1, limit: 10, offset: 0 });
    const put = vi.spyOn(alertPolicyApi, "put").mockResolvedValue(policy());
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.click(screen.getByRole("button", { name: "Choose" }));
    await user.click(await screen.findByRole("button", { name: "Choose pa-fw-01" }));
    expect(screen.getByText("pa-fw-01", { selector: ".policy-target" })).toBeInTheDocument();
    const consoles = within(screen.getByLabelText("Console")).getAllByRole("option");
    expect(consoles.map((o) => o.textContent)).toEqual(["All consoles", expect.stringMatching(/^This source's console only/)]);
    await user.selectOptions(screen.getByLabelText("Console"), INST_A);
    await user.selectOptions(screen.getByLabelText("Detector"), "NO_EVENTS");
    await user.click(screen.getByRole("radio", { name: "Digest" }));
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put).toHaveBeenCalledWith({
      instance_id: INST_A,
      scope_kind: "SOURCE",
      scope_value: SRC_FW,
      family: "log_source",
      detector_type: "NO_EVENTS",
      mode: "DIGEST",
    });
  });

  it("a property watch has no class scope and picks from the watches", async () => {
    const user = userEvent.setup();
    mount([]);
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    const put = vi.spyOn(alertPolicyApi, "put").mockResolvedValue(policy());
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.selectOptions(screen.getByLabelText("Family"), "property_watch");
    expect(screen.queryByRole("radio", { name: "A whole class" })).toBeNull();
    expect(screen.getByText("A property watch has no class scope.")).toBeInTheDocument();
    await user.selectOptions(await screen.findByLabelText("Property watch"), WATCH);
    await user.selectOptions(screen.getByLabelText("Detector"), "PW_NEW_VALUE");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put).toHaveBeenCalledWith({
      instance_id: null,
      scope_kind: "SOURCE",
      scope_value: WATCH,
      family: "property_watch",
      detector_type: "PW_NEW_VALUE",
      mode: "RECORD_ONLY",
    });
  });

  it("watches that cannot be read say why, and an empty watch list says so", async () => {
    const user = userEvent.setup();
    mount([]);
    vi.spyOn(api, "propertyWatches").mockRejectedValue(new ApiError(500, "x"));
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.selectOptions(screen.getByLabelText("Family"), "property_watch");
    expect(await screen.findByText(/The property watches could not be read:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save policy" })).toBeDisabled();
  });

  it("an identity that already has the same mode cannot be saved again; another mode says it replaces", async () => {
    const user = userEvent.setup();
    mount([policy({ id: "x", scope_value: "FIREWALL", detector_type: "VOLUME_SPIKE", mode: "RECORD_ONLY" })]);
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.click(screen.getByRole("radio", { name: "A whole class" }));
    await user.selectOptions(screen.getByLabelText("Class"), "FIREWALL");
    await user.selectOptions(screen.getByLabelText("Detector"), "VOLUME_SPIKE");
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    const form = screen.getByRole("form", { name: "Add a policy" });
    expect(within(form).getByRole("button", { name: "Save policy" })).toBeDisabled();
    expect(within(form).getByText("Nothing changed.")).toBeInTheDocument();
    expect(within(form).getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "Firewall: Volume spike is already Record only.",
    );
    await user.click(within(form).getByRole("radio", { name: "Alert" }));
    expect(within(form).getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "Firewall: Volume spike changes from Record only to Alert.",
    );
    expect(within(form).getByRole("button", { name: "Save policy" })).toBeEnabled();
  });

  it("a 422 shows the server's sentence next to the detector and keeps the input", async () => {
    const user = userEvent.setup();
    mount([]);
    vi.spyOn(alertPolicyApi, "put").mockRejectedValue(
      new ApiError(422, "unknown detector for family waf: NO_EVENTS"),
    );
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.click(screen.getByRole("radio", { name: "A whole class" }));
    await user.selectOptions(screen.getByLabelText("Class"), "WAF");
    await user.selectOptions(screen.getByLabelText("Detector"), "VOLUME_DROP");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    const detector = screen.getByLabelText("Detector");
    const err = await screen.findByText("unknown detector for family waf: NO_EVENTS");
    expect(err.parentElement).toContainElement(detector);
    expect(detector).toBeInvalid();
    expect(detector).toHaveValue("VOLUME_DROP");
    expect(screen.getByRole("button", { name: "Save policy" })).toBeEnabled();
  });

  it("a server error with no field is shown under the form and the input is kept", async () => {
    const user = userEvent.setup();
    mount([]);
    vi.spyOn(alertPolicyApi, "put").mockRejectedValue(new ApiError(500, "boom"));
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.click(screen.getByRole("radio", { name: "A whole class" }));
    await user.selectOptions(screen.getByLabelText("Class"), "DNS");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/^The policy was not saved: /);
    expect(screen.getByLabelText("Class")).toHaveValue("DNS");
  });

  it("submits once while a save is in flight", async () => {
    const user = userEvent.setup();
    mount([]);
    let release: (v: ReturnType<typeof policy>) => void = () => {};
    const put = vi.spyOn(alertPolicyApi, "put").mockReturnValue(new Promise((r) => (release = r)));
    await user.click(screen.getByRole("button", { name: "Add policy" }));
    await user.click(screen.getByRole("radio", { name: "A whole class" }));
    await user.selectOptions(screen.getByLabelText("Class"), "DNS");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(screen.getByRole("button", { name: "Saving" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Saving" }));
    expect(put).toHaveBeenCalledTimes(1);
    release(policy());
    await waitFor(() => expect(screen.queryByRole("form")).toBeNull());
  });
});

describe("removing a row", () => {
  it("states the effect first, then deletes by id, reloads and says so", async () => {
    const user = userEvent.setup();
    mount();
    const remove = vi.spyOn(alertPolicyApi, "remove").mockResolvedValue(undefined);
    await user.click(screen.getByRole("button", { name: "Remove the policy for edge-waf, Request-rate spike" }));
    expect(screen.getByRole("group", { name: "Before you remove it" })).toHaveTextContent(
      "the Digest row goes. That scope then follows a broader policy, or ALERT when none applies.",
    );
    expect(remove).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Remove policy" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("row-source-waf"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Removed the policy for edge-waf, Request-rate spike.",
    );
    expect(alertPolicyApi.list).toHaveBeenCalled();
  });

  it("Keep leaves the row alone", async () => {
    const user = userEvent.setup();
    mount();
    const remove = vi.spyOn(alertPolicyApi, "remove").mockResolvedValue(undefined);
    await user.click(screen.getByRole("button", { name: /^Remove the policy for edge-waf/ }));
    await user.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.queryByRole("group", { name: "Before you remove it" })).toBeNull();
    expect(remove).not.toHaveBeenCalled();
  });

  it("a 404 means someone else removed it: the list is refreshed and the page says so", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(alertPolicyApi, "remove").mockRejectedValue(new ApiError(404, "alert policy row not found"));
    vi.mocked(alertPolicyApi.list).mockResolvedValue(MIXED.filter((r) => r.id !== "row-source-waf"));
    await user.click(screen.getByRole("button", { name: /^Remove the policy for edge-waf/ }));
    await user.click(screen.getByRole("button", { name: "Remove policy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already removed by someone else/);
    await waitFor(() => expect(screen.queryByText("edge-waf")).toBeNull());
  });

  it("a failed delete keeps the row and says it was not removed", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(alertPolicyApi, "remove").mockRejectedValue(new ApiError(500, "boom"));
    await user.click(screen.getByRole("button", { name: /^Remove the policy for edge-waf/ }));
    await user.click(screen.getByRole("button", { name: "Remove policy" }));
    expect(await screen.findByText(/^The policy was not removed: /)).toBeInTheDocument();
    expect(screen.getByText("edge-waf")).toBeInTheDocument();
  });

  it("a 403 on delete names admin:outage", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(alertPolicyApi, "remove").mockRejectedValue(new ApiError(403, "no"));
    await user.click(screen.getByRole("button", { name: /^Remove the policy for edge-waf/ }));
    await user.click(screen.getByRole("button", { name: "Remove policy" }));
    await waitFor(() =>
      expect(document.body.textContent).toMatch(
        /admin:outage permission to remove a policy\. Nothing was removed/,
      ),
    );
  });

  it("when the reload after a change fails it says the list may be stale", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(alertPolicyApi, "remove").mockResolvedValue(undefined);
    vi.mocked(alertPolicyApi.list).mockRejectedValue(new ApiError(500, "x"));
    await user.click(screen.getByRole("button", { name: /^Remove the policy for edge-waf/ }));
    await user.click(screen.getByRole("button", { name: "Remove policy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/The list could not be reloaded/);
  });
});
