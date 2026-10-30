import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IpsProfileEditor, type IpsEditorSource } from "./IpsProfileEditor";
import {
  DEFAULT_MAPPING,
  HISTORY,
  IPS_SCHEMA,
  catalog,
  fieldReport,
  panProfile,
  profile,
  report,
} from "./fixtures";
import { ApiError, ipsApi, type IpsProfileOut } from "@/lib/api";

const SOURCE: IpsEditorSource = {
  id: "ls-413",
  name: "CiscoFirepowerThreatDefense @ ftd-01",
  qradarId: 413,
  typeName: "Cisco Firepower Threat Defense",
};

function editor(over: Partial<React.ComponentProps<typeof IpsProfileEditor>> = {}) {
  return render(
    <IpsProfileEditor
      schema={IPS_SCHEMA}
      source={SOURCE}
      profile={profile()}
      catalog={catalog()}
      history={HISTORY}
      {...over}
    />,
  );
}

const select = (name: RegExp) => screen.getByRole("combobox", { name });
const kindGroup = (label: string) =>
  screen.getByRole("group", { name: new RegExp(`${label} reference kind`, "i") });

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

async function chooseSignature(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(within(kindGroup("Signature")).getByRole("radio", { name: "Custom property" }));
  const box = screen.getByRole("combobox", { name: /signature property/i });
  await user.type(box, name);
}

describe("IpsProfileEditor: header and sections", () => {
  it("states the source, its version, validation and collection", () => {
    editor();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "CiscoFirepowerThreatDefense @ ftd-01",
    );
    expect(screen.getByText("QRadar id 413")).toBeInTheDocument();
    expect(screen.getByText("Cisco Firepower Threat Defense")).toBeInTheDocument();
    expect(screen.getByText("Version 2")).toBeInTheDocument();
    expect(screen.getByText("Valid (v2)")).toBeInTheDocument();
    expect(screen.getAllByText("Collecting").length).toBeGreaterThan(0);
  });

  it("has sections in the designed order", () => {
    editor();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Field mapping",
      "Validation",
      "Action values",
      "Collection",
      "History",
    ]);
  });
});

describe("IpsProfileEditor: the reference kind of each field", () => {
  it("offers the schema's normalized options, and nothing else, for a QRadar field", () => {
    editor();
    const box = select(/source ip qradar field/i);
    expect(box.tagName).toBe("SELECT");
    expect(box).toHaveValue("sourceip");
    const options = within(box)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(IPS_SCHEMA.fields[0].normalized_options);
    expect(within(kindGroup("Source IP")).getByRole("radio", { name: "QRadar field" })).toBeChecked();
  });

  it("switches a field to a custom property and back, restoring the schema default", async () => {
    const user = userEvent.setup();
    editor();
    await user.click(within(kindGroup("Signature")).getByRole("radio", { name: "Custom property" }));
    const box = screen.getByRole("combobox", { name: /signature property/i });
    expect(box).toHaveValue("");
    await user.click(box);
    // The searchable picker lists the catalog, and no normalized field.
    const list = screen.getByRole("listbox");
    expect(within(list).getByRole("option", { name: /PAN_ThreatID/ })).toBeInTheDocument();
    expect(within(list).queryByRole("option", { name: /^sourceip/ })).not.toBeInTheDocument();
    await user.click(within(kindGroup("Signature")).getByRole("radio", { name: "QRadar field" }));
    expect(select(/signature qradar field/i)).toHaveValue("qid");
  });

  it("does not warn about the property list for a normalized field", () => {
    editor();
    expect(screen.queryByText(/Not in this console’s property list/)).not.toBeInTheDocument();
  });

  it("warns, without blocking, about a custom property the list does not have", async () => {
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "My_Threat");
    expect(screen.getByText(/Not in this console’s property list/)).toBeInTheDocument();
  });

  it("cannot unmap a required field, and can unmap an optional one", async () => {
    const user = userEvent.setup();
    editor();
    expect(screen.queryByRole("button", { name: "Do not map Source IP" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Do not map Signature" })).not.toBeInTheDocument();
    // A required field's select has no empty choice while it holds a value.
    expect(
      within(select(/source ip qradar field/i))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).not.toContain("Choose a field");
    await user.click(screen.getByRole("button", { name: "Do not map Protocol" }));
    expect(select(/protocol qradar field/i)).toHaveValue("");
    expect(screen.getByText("1 unsaved change.")).toBeInTheDocument();
  });

  it("sends the kind with each name, and null for an unmapped field", async () => {
    const save = vi.spyOn(ipsApi, "saveProfile").mockResolvedValue(
      profile({ profile_version: 3, validation_status: "UNVALIDATED", validated_profile_version: null }),
    );
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    const dialog = screen.getByRole("region", { name: "Confirm the change" });
    expect(within(dialog).getByRole("list", { name: "Changed fields" })).toHaveTextContent(
      "Signature: qid → PAN_ThreatID (custom property)",
    );
    expect(save).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Save as version 3" }));
    expect(save).toHaveBeenCalledWith(
      "ls-413",
      expect.objectContaining({
        expected_profile_version: 2,
        // The stored preset is sent back: a save replaces it.
        preset: "CISCO_FTD",
        mapping: expect.objectContaining({
          SIGNATURE: { kind: "CUSTOM_PROPERTY", name: "PAN_ThreatID" },
          SOURCE_IP: { kind: "NORMALIZED", name: "sourceip" },
          ACTION: null,
        }),
        action_value_map: { BLOCKED: [], ALLOWED: [] },
      }),
    );
  });

  it("counts a kind change of the same name as a change", async () => {
    const user = userEvent.setup();
    editor();
    await user.click(within(kindGroup("Severity")).getByRole("radio", { name: "Custom property" }));
    expect(screen.getByText("1 unsaved change.")).toBeInTheDocument();
    await user.type(screen.getByRole("combobox", { name: /severity property/i }), "severity");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    expect(screen.getByRole("list", { name: "Changed fields" })).toHaveTextContent(
      "Severity: severity → severity (custom property)",
    );
  });

  it("blocks an empty required field and says why, sending nothing", async () => {
    const save = vi.spyOn(ipsApi, "saveProfile");
    const user = userEvent.setup();
    editor();
    await user.click(within(kindGroup("Signature")).getByRole("radio", { name: "Custom property" }));
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    expect(screen.getByText(/collection cannot run without it/i)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Confirm the change" })).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("blocks a malformed custom property name in the browser", async () => {
    const save = vi.spyOn(ipsApi, "saveProfile");
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "a;b");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    expect(screen.getByText(/Not saved\. Fix the fields marked below/)).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });
});

describe("IpsProfileEditor: creating from a preset", () => {
  function create() {
    return editor({ profile: null, history: [] });
  }

  it("offers both presets and says a preset must still validate", () => {
    create();
    const group = screen.getByRole("group", { name: "Start from a preset" });
    expect(within(group).getByRole("radio", { name: "Cisco Firepower Threat Defense" })).toBeInTheDocument();
    expect(within(group).getByRole("radio", { name: "Palo Alto THREAT" })).toBeInTheDocument();
    expect(group).toHaveTextContent(/still has to validate/);
    expect(screen.getByText("No IPS profile", { exact: false })).toBeInTheDocument();
  });

  it("fills the form from the chosen preset and sends it as the preset", async () => {
    const preset = {
      ...IPS_SCHEMA,
      presets: [
        IPS_SCHEMA.presets[0],
        {
          ...IPS_SCHEMA.presets[1],
          mapping: { ...DEFAULT_MAPPING, SIGNATURE: { kind: "CUSTOM_PROPERTY" as const, name: "PAN_ThreatID" } },
        },
      ],
    };
    const save = vi.spyOn(ipsApi, "saveProfile").mockResolvedValue(
      profile({ profile_version: 1, validation_status: "UNVALIDATED", validated_profile_version: null, enabled: false }),
    );
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor({ schema: preset, profile: null, history: [] });
    expect(select(/signature qradar field/i)).toHaveValue("qid");
    await user.click(screen.getByRole("radio", { name: "Palo Alto THREAT" }));
    expect(screen.getByRole("combobox", { name: /signature property/i })).toHaveValue("PAN_ThreatID");
    expect(within(kindGroup("Signature")).getByRole("radio", { name: "Custom property" })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Review and create" }));
    const dialog = screen.getByRole("region", { name: "Confirm the change" });
    expect(dialog).toHaveTextContent("disabled");
    expect(dialog).toHaveTextContent(/only filled in defaults; they are validated/);
    await user.click(within(dialog).getByRole("button", { name: "Create profile" }));
    expect(save).toHaveBeenCalledWith(
      "ls-413",
      expect.objectContaining({
        expected_profile_version: null,
        preset: "PALO_ALTO_THREAT",
        mapping: expect.objectContaining({
          SIGNATURE: { kind: "CUSTOM_PROPERTY", name: "PAN_ThreatID" },
        }),
      }),
    );
    expect(await screen.findByText(/Created version 1\. Collection stays off/)).toBeInTheDocument();
  });

  it("explains that Action is unmapped by default, and why", () => {
    create();
    const note = screen.getByText(/Action is not mapped/);
    expect(note.parentElement).toHaveTextContent(/Neither Cisco Firepower Threat Defense nor Palo Alto THREAT exposes an action/);
    expect(note.parentElement).toHaveTextContent(/block ratio cannot be judged/);
    expect(note.parentElement).toHaveTextContent(/never as no blocks/);
  });

  it("returns to the schema defaults for no preset", async () => {
    const user = userEvent.setup();
    create();
    await user.click(screen.getByRole("radio", { name: "Palo Alto THREAT" }));
    await user.click(screen.getByRole("radio", { name: /No preset/ }));
    expect(select(/source ip qradar field/i)).toHaveValue("sourceip");
  });

  it("shows only the mapping section until the profile exists", () => {
    create();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Field mapping",
    ]);
  });
});

describe("IpsProfileEditor: the Action value map", () => {
  it("explains an unmapped Action instead of offering a value map", () => {
    editor();
    const section = screen.getByRole("region", { name: "Action values" });
    expect(section).toHaveTextContent(/Action is not mapped/);
    expect(section).toHaveTextContent(/block ratio cannot be judged/);
    expect(within(section).queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows the discovered values with their counts once Action is mapped", () => {
    editor({ profile: panProfile(), source: { ...SOURCE, id: "ls-412" } });
    const section = screen.getByRole("region", { name: "Action values" });
    expect(within(section).getByRole("table")).toBeInTheDocument();
    expect(within(section).getByText("alert")).toBeInTheDocument();
    expect(within(section).getByText("5,200")).toBeInTheDocument();
    expect(within(section).getByText("drop")).toBeInTheDocument();
  });

  it("assigns a value and saves the map as a mapping change", async () => {
    const save = vi.spyOn(ipsApi, "saveProfile").mockResolvedValue(panProfile({ profile_version: 4 }));
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor({ profile: panProfile(), source: { ...SOURCE, id: "ls-412" } });
    const group = screen.getByRole("group", { name: /Action value drop/ });
    await user.click(within(group).getByRole("radio", { name: "Blocked" }));
    const section = screen.getByRole("region", { name: "Action values" });
    await user.click(within(section).getByRole("button", { name: "Review and save" }));
    const dialog = within(section).getByRole("region", { name: "Confirm the change" });
    expect(within(dialog).getByRole("list", { name: "Changed values" })).toHaveTextContent("Blocked values: + drop");
    await user.click(within(dialog).getByRole("button", { name: "Save as version 4" }));
    expect(save).toHaveBeenCalledWith(
      "ls-412",
      expect.objectContaining({
        action_value_map: { BLOCKED: ["reset-both", "drop"], ALLOWED: ["alert"] },
        mapping: expect.objectContaining({ ACTION: { kind: "CUSTOM_PROPERTY", name: "PAN_Action" } }),
      }),
    );
  });

  it("explains that truncated values are why validation was inconclusive", () => {
    editor({ profile: panProfile(), source: { ...SOURCE, id: "ls-412" } });
    const section = screen.getByRole("region", { name: "Action values" });
    // One banner, saying both facts once.
    const banners = within(section).getAllByRole("status");
    expect(banners).toHaveLength(1);
    expect(banners[0]).toHaveTextContent(/at most 20 action values/);
    expect(banners[0]).toHaveTextContent(/last validation was inconclusive/);
    expect(banners[0]).toHaveTextContent(/never as allowed/);
    expect(within(section).queryByText(/This list is truncated/)).not.toBeInTheDocument();
  });

  it("does not call the validation inconclusive when it was not", () => {
    const pan = panProfile();
    editor({
      profile: panProfile({
        validation_status: "VALID",
        validation: { ...pan.validation!, status: "VALID", reasons: [] },
      }),
      source: { ...SOURCE, id: "ls-412" },
    });
    const section = screen.getByRole("region", { name: "Action values" });
    expect(section).not.toHaveTextContent(/inconclusive/);
    expect(within(section).getAllByRole("status")).toHaveLength(1);
    expect(within(section).getByText(/This list is truncated/)).toBeInTheDocument();
  });

  it("does not tie a truncated list to inconclusive when the result is another version's", () => {
    editor({
      profile: panProfile({ profile_version: 4, validation_status: "UNVALIDATED" }),
      source: { ...SOURCE, id: "ls-412" },
    });
    const section = screen.getByRole("region", { name: "Action values" });
    expect(section).not.toHaveTextContent(/inconclusive/);
    expect(within(section).getAllByRole("status")).toHaveLength(1);
    expect(within(section).getByText(/This list is truncated/)).toBeInTheDocument();
  });
});

describe("IpsProfileEditor: the validation report", () => {
  it("shows the window in UTC, populated shares in percent and no query text", () => {
    editor();
    const section = screen.getByRole("region", { name: "Validation" });
    expect(section).toHaveTextContent("Window (UTC)");
    expect(section).toHaveTextContent(/UTC/);
    expect(section).toHaveTextContent("99.9 %");
    expect(section).toHaveTextContent("10,000");
    expect(section).not.toHaveTextContent(/SELECT|FROM events|WHERE/i);
    // IPS measures neither distinct values nor plausibility.
    expect(within(section).queryByRole("columnheader", { name: /Distinct values/ })).not.toBeInTheDocument();
    expect(within(section).queryByRole("columnheader", { name: /Plausible/ })).not.toBeInTheDocument();
  });

  it("names each field's reference kind", () => {
    editor({ profile: panProfile(), source: { ...SOURCE, id: "ls-412" } });
    const row = screen.getByRole("row", { name: /^Action/ });
    expect(row).toHaveTextContent("· Custom property ·");
    expect(screen.getByRole("row", { name: /^Source IP/ })).toHaveTextContent("· QRadar field ·");
    expect(row).toHaveTextContent("· Custom property ·");
  });

  it("warns, as a warning and not a failure, about the generic signature-name share", () => {
    editor({
      profile: profile({
        validation: report({
          warnings: ["SIGNATURE: 12.4% of events use a generic Unknown QID name"],
          fields: [
            ...report().fields.filter((f) => f.field !== "SIGNATURE"),
            fieldReport({
              field: "SIGNATURE",
              reference: { kind: "NORMALIZED", name: "qid" },
              populated_ratio: 1,
              generic_name_events: 1240,
              generic_name_ratio: 0.124,
              warnings: ["SIGNATURE: 12.4% of events use a generic Unknown QID name"],
            }),
          ],
        }),
      }),
    });
    const warning = screen.getByText(/12\.4 % of events carry a generic “Unknown …” signature name/);
    expect(warning).toHaveClass("is-warn");
    expect(warning).toHaveTextContent(/does not stop collection/);
    // The result stays valid.
    expect(screen.getByText("Valid (v2)")).toBeInTheDocument();
    expect(screen.queryByText(/12\.4% of events use a generic Unknown QID name/)).not.toBeInTheDocument();
  });

  it("does not warn when no event carries a generic name", () => {
    editor();
    expect(screen.queryByText(/generic “Unknown/)).not.toBeInTheDocument();
  });

  it("reads INVALID in words, per field", () => {
    editor({
      profile: profile({
        validation_status: "INVALID",
        collection_eligible: false,
        ineligible_reason: "profile validation is INVALID",
        validation: report({
          status: "INVALID",
          reasons: [
            "SOURCE_IP: INSUFFICIENT_COVERAGE (populated on 62.0% of events, below the required 90%)",
            "SIGNATURE: FIELD_REJECTED (QRadar rejected the mapped field as named)",
          ],
          fields: [
            fieldReport({ populated_ratio: 0.62, status: "INSUFFICIENT_COVERAGE" }),
            fieldReport({ field: "SIGNATURE", reference: { kind: "CUSTOM_PROPERTY", name: "PAN_Thret" }, status: "FIELD_REJECTED", populated_ratio: null }),
          ],
        }),
      }),
    });
    const reasons = screen.getByRole("list", { name: "Reasons for the result" });
    expect(reasons).toHaveTextContent("Source IP: Only 62.0 % of events carry it; collection needs 90.0 %.");
    expect(reasons).toHaveTextContent("Signature: QRadar does not accept this field as named.");
    expect(screen.getAllByText("Invalid").length).toBeGreaterThan(0);
    expect(screen.getByText("Field rejected")).toBeInTheDocument();
  });

  it("reads INCONCLUSIVE as no verdict, never as a failing field", () => {
    editor({
      profile: profile({
        validation_status: "INCONCLUSIVE",
        collection_eligible: false,
        validation: report({
          status: "INCONCLUSIVE",
          total_events: 0,
          reasons: ["SOURCE_IP: NO_EVENTS (the source produced no events in the window; nothing can be concluded)"],
          fields: [fieldReport({ status: "NO_EVENTS", populated_ratio: null, populated_events: null })],
        }),
      }),
    });
    expect(screen.getAllByText("Inconclusive").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/This says nothing about the field/).length).toBeGreaterThan(0);
  });

  it("words the truncated-probe reason", () => {
    editor({ profile: panProfile(), source: { ...SOURCE, id: "ls-412" } });
    expect(screen.getByRole("list", { name: "Reasons for the result" })).toHaveTextContent(
      "The bounded validation probe was truncated, so the result is inconclusive rather than valid.",
    );
  });

  it("shows a stale VALID as not validated for the current version", () => {
    editor({
      profile: profile({
        profile_version: 3,
        validation_status: "VALID",
        validated_profile_version: 2,
        collection_eligible: false,
        ineligible_reason: "profile version 3 has not been validated (last validated: 2)",
      }),
    });
    expect(screen.getAllByText("Version 3 not validated").length).toBeGreaterThan(0);
    expect(screen.queryByText("Valid (v3)")).not.toBeInTheDocument();
    expect(screen.getByText(/This report is for version 2; version 3 has not been validated/)).toBeInTheDocument();
  });

  it("shows UNVALIDATED with no report, saying why", () => {
    editor({
      profile: profile({
        profile_version: 1,
        validation_status: "UNVALIDATED",
        validated_profile_version: null,
        validated_at: null,
        validation: null,
        enabled: false,
        collection_eligible: false,
      }),
    });
    expect(screen.getByText(/Version 1 has not been validated, so there is no report/)).toBeInTheDocument();
  });
});

describe("IpsProfileEditor: validation requests", () => {
  it("requests a validation and shows it pending", async () => {
    const out = profile({ validation_pending: true, validation_requested_at: "2026-10-13T09:10:00Z" });
    const validate = vi.spyOn(ipsApi, "validateProfile").mockResolvedValue(out);
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Validate again" }));
    expect(validate).toHaveBeenCalledWith("ls-413");
    expect(await screen.findByText("Validation of version 2 has been requested.")).toBeInTheDocument();
    expect(screen.getByText(/Validating version 2/)).toHaveTextContent("since 09:10 UTC");
    expect(screen.getByText("Validating v2…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validation running" })).toBeDisabled();
  });

  it("shows the server's reason on a 409 while a validation is pending", async () => {
    vi.spyOn(ipsApi, "validateProfile").mockRejectedValue(
      new ApiError(409, "Conflict", { message: "validation of version 2 is already running", current_version: 2, updated_by: null, pending_since: "2026-10-13T09:10:00Z" }),
    );
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Validate again" }));
    const alert = await screen.findByText(/Validation was not started\./);
    expect(alert).toHaveTextContent("already running");
  });

  it("polls every 5 seconds while pending and stops when it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const done = profile({ validation_pending: false });
    const read = vi.spyOn(ipsApi, "profile").mockResolvedValue(done);
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    editor({
      profile: profile({ validation_pending: true, validation_requested_at: "2026-10-13T09:10:00Z" }),
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999);
    });
    expect(read).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(read).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Validating version/)).not.toBeInTheDocument();
  });

  it("says the validation did not run when the source was busy, and keeps the previous result", () => {
    editor({
      history: [
        {
          at: "2026-10-13T09:20:00Z",
          actor: "system",
          action: "ips.profile.validated",
          profile_version: 2,
          mapping_before: null,
          mapping_after: null,
          outcome: "SOURCE_BUSY",
          reasons: null,
        },
        ...HISTORY,
      ],
    });
    const section = screen.getByRole("region", { name: "Validation" });
    expect(section).toHaveTextContent(/did not run: another job held this source/);
    expect(section).toHaveTextContent(/nothing was changed/);
    // The previous result is still the one shown.
    expect(screen.getByText("Valid (v2)")).toBeInTheDocument();
    const history = screen.getByRole("region", { name: "History" });
    expect(history).toHaveTextContent(/could not take the source lock, so it did not run/);
  });

  it("drops the busy notice once a newer request exists", () => {
    editor({
      history: [
        {
          at: "2026-10-13T09:25:00Z",
          actor: "a.rivera",
          action: "ips.profile.validation_requested",
          profile_version: 2,
          mapping_before: null,
          mapping_after: null,
          outcome: "SUCCESS",
          reasons: null,
        },
        {
          at: "2026-10-13T09:20:00Z",
          actor: "system",
          action: "ips.profile.validated",
          profile_version: 2,
          mapping_before: null,
          mapping_after: null,
          outcome: "SOURCE_BUSY",
          reasons: null,
        },
      ],
    });
    expect(screen.queryByText(/did not run: another job held this source/)).not.toBeInTheDocument();
  });
});

describe("IpsProfileEditor: concurrent changes", () => {
  it("keeps the edits, names the author of the newer version and offers a reload", async () => {
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      new ApiError(409, "Conflict", { message: "profile version conflict; current version is 3", current_version: 3, updated_by: "j.ito", pending_since: null }),
    );
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    const alert = await screen.findByText(/profile version conflict; current version is 3\./);
    expect(alert).toHaveTextContent("Your edits are kept below; reload to see theirs.");
    expect(screen.getByRole("button", { name: "Reload their version" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /signature property/i })).toHaveValue("PAN_ThreatID");
  });

  it("names the newer version and author when the 409 carries no message", async () => {
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      new ApiError(409, "Conflict", { current_version: 3, updated_by: "j.ito" }),
    );
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    expect(
      await screen.findByText(/This mapping changed to version 3 while you were editing \(by j\.ito\)/),
    ).toBeInTheDocument();
  });

  it("loads their version on request and rebases on it, keeping the edits", async () => {
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      new ApiError(409, "Conflict", { message: "profile version conflict", current_version: 3, updated_by: null, pending_since: null }),
    );
    const theirs = profile({ profile_version: 3, mapping: { ...DEFAULT_MAPPING, SEVERITY: null } });
    vi.spyOn(ipsApi, "profile").mockResolvedValue(theirs);
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    await user.click(await screen.findByRole("button", { name: "Reload their version" }));
    expect(await screen.findByText(/Loaded version 3\. Your edits are still in the form/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reload their version" })).not.toBeInTheDocument();
    // Diffed against their version: the signature edit, and the severity they unmapped.
    expect(screen.getByText("2 unsaved changes.")).toBeInTheDocument();
  });
});

describe("IpsProfileEditor: errors", () => {
  it("names admin:ips on a 403 and says nothing changed", async () => {
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    const alert = await screen.findByText(/Your role cannot change IPS sources/);
    expect(alert).toHaveTextContent("admin:ips");
    expect(alert).toHaveTextContent("Nothing was changed.");
    expect(alert).not.toHaveTextContent("admin:waf");
    expect(screen.getByRole("combobox", { name: /signature property/i })).toHaveValue("PAN_ThreatID");
  });

  it("shows a 422 message at the field it names", async () => {
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      new ApiError(422, "Unprocessable", { message: "invalid QRadar property name: PAN_ThreatID" }),
    );
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    expect(await screen.findAllByText("invalid QRadar property name: PAN_ThreatID")).not.toHaveLength(0);
    expect(screen.getByRole("combobox", { name: /signature property/i })).toHaveAttribute("aria-invalid", "true");
  });

  it("does not leak a server error", async () => {
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(new ApiError(500, "psycopg2 trace"));
    const user = userEvent.setup();
    editor();
    await chooseSignature(user, "PAN_ThreatID");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    expect(await screen.findByText(/Not changed\./)).not.toHaveTextContent("psycopg2");
  });

  it("says the history could not be loaded, which is not the same as empty", () => {
    editor({ history: null });
    expect(screen.getByText(/The history could not be loaded/)).toBeInTheDocument();
  });

  it("says an empty history is empty", () => {
    editor({ history: [] });
    expect(screen.getByText(/No changes are recorded for this profile yet/)).toBeInTheDocument();
  });

  it("degrades to free text when the property list is unavailable, and says why", async () => {
    const user = userEvent.setup();
    editor({ catalog: null });
    expect(screen.getByText(/The property list is unavailable/)).toBeInTheDocument();
    await user.click(within(kindGroup("Signature")).getByRole("radio", { name: "Custom property" }));
    expect(screen.getByRole("combobox", { name: /signature property/i })).toBeInTheDocument();
  });
});

describe("IpsProfileEditor: collection", () => {
  function notValid(over: Partial<IpsProfileOut>) {
    return profile({ enabled: false, collection_eligible: false, ...over });
  }

  it("refuses to enable an unvalidated version and gives the reason", () => {
    editor({
      profile: notValid({
        profile_version: 3,
        validation_status: "UNVALIDATED",
        validated_profile_version: 2,
      }),
    });
    const button = screen.getByRole("button", { name: "Enable collection" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(
      "Version 3 has not been validated. Validate it first.",
    );
  });

  it("refuses to enable an invalid or an inconclusive version, each with its reason", () => {
    const { unmount } = editor({ profile: notValid({ validation_status: "INVALID" }) });
    expect(screen.getByRole("button", { name: "Enable collection" })).toBeDisabled();
    expect(screen.getByText(/validated as invalid\. Fix the mapping/)).toBeInTheDocument();
    unmount();
    editor({ profile: notValid({ validation_status: "INCONCLUSIVE" }) });
    expect(screen.getByRole("button", { name: "Enable collection" })).toBeDisabled();
    expect(screen.getByText(/validated as inconclusive\. Validate again/)).toBeInTheDocument();
  });

  it("refuses to enable while a validation is pending", () => {
    editor({
      profile: notValid({ validation_pending: true, validation_requested_at: "2026-10-13T09:10:00Z" }),
    });
    expect(screen.getByRole("button", { name: "Enable collection" })).toBeDisabled();
    expect(screen.getByText(/still being validated/)).toBeInTheDocument();
  });

  it("enables a valid version", async () => {
    const enable = vi.spyOn(ipsApi, "enableProfile").mockResolvedValue(profile());
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor({ profile: notValid({}) });
    await user.click(screen.getByRole("button", { name: "Enable collection" }));
    expect(enable).toHaveBeenCalledWith("ls-413");
    expect(await screen.findByText("Collection is enabled for version 2.")).toBeInTheDocument();
  });

  it("shows the server's refusal when enabling fails with a 409", async () => {
    vi.spyOn(ipsApi, "enableProfile").mockRejectedValue(
      new ApiError(409, "Conflict", { message: "profile version 2 is not VALID", current_version: 2, updated_by: null, pending_since: null }),
    );
    const user = userEvent.setup();
    editor({ profile: notValid({}) });
    await user.click(screen.getByRole("button", { name: "Enable collection" }));
    expect(await screen.findByText(/Not enabled\. profile version 2 is not VALID/)).toBeInTheDocument();
  });

  it("disables collection", async () => {
    const disable = vi.spyOn(ipsApi, "disableProfile").mockResolvedValue(notValid({}));
    vi.spyOn(ipsApi, "profileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Disable collection" }));
    expect(disable).toHaveBeenCalledWith("ls-413");
    expect(await screen.findByText(/Collection is disabled\. No searches run/)).toBeInTheDocument();
  });

  it("names admin:ips when enabling is forbidden", async () => {
    vi.spyOn(ipsApi, "enableProfile").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    editor({ profile: notValid({}) });
    await user.click(screen.getByRole("button", { name: "Enable collection" }));
    expect(await screen.findByText(/permission admin:ips/)).toBeInTheDocument();
  });
});

describe("IpsProfileEditor: history", () => {
  it("words the audit actions and shows the kind of each reference", () => {
    editor();
    const history = screen.getByRole("region", { name: "History" });
    expect(history).toHaveTextContent("Validated");
    expect(history).toHaveTextContent("Mapping changed");
    expect(history).toHaveTextContent("Signature: PAN_ThreatID (custom property) → qid");
    expect(history).toHaveTextContent("Times are UTC");
  });
});
