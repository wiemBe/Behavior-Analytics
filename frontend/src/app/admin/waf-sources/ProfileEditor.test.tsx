import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileEditor, type EditorSource } from "./ProfileEditor";
import {
  HISTORY,
  SCHEMA,
  catalog,
  profile,
  status,
} from "@/components/fieldmapping/fixtures";
import { ApiError, api, type WafProfileOut } from "@/lib/api";
import { draftFromProfile } from "@/lib/fieldMapping";

const SOURCE: EditorSource = {
  id: "ls-1",
  name: "WAF edge - eu-west",
  qradarId: 212,
  typeName: "Generic WAF DSM",
  instanceId: "inst-1",
};

function editor(over: Partial<React.ComponentProps<typeof ProfileEditor>> = {}) {
  return render(
    <ProfileEditor
      schema={SCHEMA}
      source={SOURCE}
      profile={profile()}
      catalog={catalog()}
      history={HISTORY}
      siblings={[]}
      prefill={null}
      {...over}
    />,
  );
}

const input = (name: RegExp) => screen.getByRole("combobox", { name });

async function editUrl(user: ReturnType<typeof userEvent.setup>, value = "request_uri") {
  const el = input(/url path property/i);
  await user.clear(el);
  await user.type(el, value);
}

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("ProfileEditor: header", () => {
  it("states the source, its version, validation and collection", () => {
    editor();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF edge - eu-west");
    expect(screen.getByText("QRadar id 212")).toBeInTheDocument();
    expect(screen.getByText("Generic WAF DSM")).toBeInTheDocument();
    expect(screen.getByText("Acme WAF")).toBeInTheDocument();
    expect(screen.getByText("Version 4")).toBeInTheDocument();
    expect(screen.getByText("Valid (v4)")).toBeInTheDocument();
    expect(screen.getAllByText("Collecting").length).toBeGreaterThan(0);
  });

  it("shows an invalid source's reason in the header", () => {
    editor({
      profile: profile({
        validation_status: "INVALID",
        collection_eligible: false,
        ineligible_reason: "validation is INVALID",
        validation: {
          ...profile().validation!,
          status: "INVALID",
          fields: [{ ...profile().validation!.fields[0], property_name: "WAF_URI", status: "PROPERTY_REJECTED" }],
        },
      }),
    });
    expect(screen.getByText("Invalid: URL path property does not exist")).toBeInTheDocument();
    expect(screen.getAllByText("Paused — validation is INVALID").length).toBeGreaterThan(0);
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

describe("ProfileEditor: saving a change", () => {
  it("sends nothing until the confirmation is accepted, and lists each changed field", async () => {
    const save = vi.spyOn(api, "saveWafProfile");
    const user = userEvent.setup();
    editor();
    expect(screen.getByRole("button", { name: "Review and save" })).toBeDisabled();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    const dialog = screen.getByRole("region", { name: "Confirm the change" });
    expect(within(dialog).getByRole("list", { name: "Changed fields" })).toHaveTextContent(
      "URL path: URL → request_uri",
    );
    expect(dialog).toHaveTextContent(
      "Collection from this source pauses until version 5 validates. Validation starts now and runs a few bounded searches against QRadar.",
    );
    expect(save).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("region", { name: "Confirm the change" })).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    expect(input(/url path property/i)).toHaveValue("request_uri");
  });

  it("saves against the version being edited and shows the pending validation", async () => {
    const next = profile({
      profile_version: 5, validation_status: "UNVALIDATED", validated_profile_version: null,
      mapping: { ...profile().mapping, URL: "request_uri" }, validation_pending: true,
      validation_requested_at: "2026-10-13T00:10:00Z", collection_eligible: false,
      ineligible_reason: "version 5 is not validated",
    });
    const save = vi.spyOn(api, "saveWafProfile").mockResolvedValue(next);
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    expect(save).toHaveBeenCalledWith("ls-1", expect.objectContaining({
      expected_profile_version: 4,
      mapping: expect.objectContaining({ URL: "request_uri", VIRTUAL_HOST: null }),
    }));
    expect(await screen.findByText(/Saved as version 5\. Collection is paused/)).toBeInTheDocument();
    expect(screen.getByText(/Validating version 5/)).toHaveTextContent("since 00:10 UTC");
    expect(screen.getByText("Validating v5…")).toBeInTheDocument();
    expect(screen.getByText("No unsaved changes.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validation running" })).toBeDisabled();
  });

  it("blocks a malformed property name in the browser and sends nothing", async () => {
    const save = vi.spyOn(api, "saveWafProfile");
    const user = userEvent.setup();
    editor();
    await editUrl(user, "a;b");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    expect(screen.getByText(/Not saved\. Fix the fields marked below/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Confirm the change" })).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("blocks an empty required field and says why", async () => {
    const save = vi.spyOn(api, "saveWafProfile");
    const user = userEvent.setup();
    editor();
    await user.clear(input(/url path property/i));
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    expect(screen.getByText(/collection cannot run without it/i)).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("treats a label-only change as not affecting validation", async () => {
    const save = vi.spyOn(api, "saveWafProfile").mockResolvedValue(profile({ product_label: "New label" }));
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    const label = screen.getByLabelText("Product label");
    await user.clear(label);
    await user.type(label, "New label");
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    const dialog = screen.getByRole("region", { name: "Confirm the change" });
    expect(dialog).toHaveTextContent(/only the product label changes/i);
    expect(dialog).not.toHaveTextContent(/pauses until/i);
    await user.click(within(dialog).getByRole("button", { name: "Save label" }));
    expect(save).toHaveBeenCalledOnce();
    expect(await screen.findByText(/label change only/i)).toBeInTheDocument();
  });

  it("keeps the edits and names the newer version on a 409", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      new ApiError(409, "conflict", { current_version: 5, updated_by: "j.ito" }),
    );
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    const alert = await screen.findByText(/This mapping changed to version 5 while you were editing \(by j\.ito\)/);
    expect(alert).toHaveTextContent("Your edits are kept below; reload to see theirs.");
    expect(input(/url path property/i)).toHaveValue("request_uri");
    expect(screen.getByText("1 unsaved change.")).toBeInTheDocument();
  });

  it("loads their version on request, keeping the edits and rebasing on it", async () => {
    const theirs = profile({ profile_version: 5, mapping: { ...profile().mapping, METHOD: "Method2" } });
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(new ApiError(409, "Conflict", { message: "Profile is at version 5", current_version: 5, updated_by: null, pending_since: null }));
    vi.spyOn(api, "wafProfile").mockResolvedValue(theirs);
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    await user.click(await screen.findByRole("button", { name: "Reload their version" }));
    expect(await screen.findByText(/Loaded version 5\. Your edits are still in the form/)).toBeInTheDocument();
    expect(screen.queryByText(/changed to version 5 while you were editing/)).not.toBeInTheDocument();
    expect(input(/url path property/i)).toHaveValue("request_uri");
    // The form is diffed against their version now: METHOD differs from the draft.
    expect(screen.getByText("2 unsaved changes.")).toBeInTheDocument();
  });

  it("shows the server's 422 at the field it names", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      new ApiError(422, "Property name 'request_uri' is reserved"),
    );
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    expect(await screen.findByText("Property name 'request_uri' is reserved")).toBeInTheDocument();
    expect(input(/url path property/i)).toHaveAttribute("aria-invalid", "true");
    expect(input(/url path property/i)).toHaveValue("request_uri");
  });

  it("shows a 409 with the server's own message", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      new ApiError(409, "Conflict", { message: "Profile is at version 7.", current_version: 7, updated_by: "k", pending_since: null }),
    );
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    expect(await screen.findByText(/Profile is at version 7\. Your edits are kept below/)).toBeInTheDocument();
  });

  it("reads a WafProfileError 422 from detail.message", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      new ApiError(422, "Unprocessable", { message: "An action map needs an action property" }),
    );
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    expect(await screen.findByText("An action map needs an action property")).toBeInTheDocument();
  });

  it("shows a 422 that names no field at the form", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      new ApiError(422, "An action map needs an action property"),
    );
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    const msg = await screen.findByText("An action map needs an action property");
    expect(msg).toHaveAttribute("role", "alert");
  });

  it("names the missing permission on a 403 and says nothing changed", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    const alert = await screen.findByText(/Your role cannot change WAF sources/);
    expect(alert).toHaveTextContent("admin:waf");
    expect(alert).toHaveTextContent("Nothing was changed.");
    expect(input(/url path property/i)).toHaveValue("request_uri");
  });

  it("does not leak a server error", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(new ApiError(500, "psycopg2 trace"));
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    const alert = await screen.findByText(/Not changed\./);
    expect(alert).not.toHaveTextContent("psycopg2");
  });

  it("discards edits", async () => {
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(input(/url path property/i)).toHaveValue("URL");
    expect(screen.getByText("No unsaved changes.")).toBeInTheDocument();
  });
});

describe("ProfileEditor: validation", () => {
  it("requests a new validation", async () => {
    const out = profile({ validation_pending: true, validation_requested_at: "2026-10-13T00:10:00Z" });
    const validate = vi.spyOn(api, "validateWafProfile").mockResolvedValue(out);
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Validate again" }));
    expect(validate).toHaveBeenCalledWith("ls-1");
    expect(await screen.findByText("Validation of version 4 has been requested.")).toBeInTheDocument();
    expect(screen.getByText(/Validating version 4/)).toHaveTextContent("since 00:10 UTC");
  });

  it("shows the server's reason when a validation is already running", async () => {
    vi.spyOn(api, "validateWafProfile").mockRejectedValue(
      new ApiError(409, "validation of version 4 is already running since 23:52 UTC"),
    );
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Validate again" }));
    const alert = await screen.findByText(/Validation was not started\./);
    expect(alert).toHaveTextContent("already running since 23:52 UTC");
  });

  it("says validation applies to the saved version when there are unsaved edits", async () => {
    const user = userEvent.setup();
    editor();
    await editUrl(user);
    expect(screen.getByText("Validates the saved version 4, not your unsaved edits.")).toBeInTheDocument();
  });

  it("polls every 5 seconds while pending and stops when it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const done = profile({ profile_version: 5, validation_status: "VALID", validated_profile_version: 5, validation_pending: false,
      validation: { ...profile().validation!, profile_version: 5 } });
    const poll = vi.spyOn(api, "wafProfile").mockResolvedValue(done);
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    editor({
      profile: profile({ profile_version: 5, validation_status: "UNVALIDATED", validated_profile_version: null,
        validation_pending: true, validation_requested_at: "2026-10-13T00:10:00Z", collection_eligible: false }),
    });
    expect(screen.getByText(/Validating version 5/)).toHaveTextContent("since 00:10 UTC");
    await act(async () => { await vi.advanceTimersByTimeAsync(4999); });
    expect(poll).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(poll).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/Validating version 5/)).not.toBeInTheDocument();
    expect(screen.getByText("Valid (v5)")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("keeps polling when a poll fails, and keeps the last state", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const poll = vi.spyOn(api, "wafProfile").mockRejectedValue(new ApiError(502, "bad gateway"));
    editor({ profile: profile({ validation_pending: true, validation_requested_at: "2026-10-13T00:10:00Z" }) });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(poll).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Validating version 4/)).toBeInTheDocument();
  });

  it("does not poll when nothing is pending", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const poll = vi.spyOn(api, "wafProfile");
    editor();
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(poll).not.toHaveBeenCalled();
  });

  it("surfaces a mapping changed by someone else that a poll reveals", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.spyOn(api, "wafProfile").mockResolvedValue(
      profile({ profile_version: 5, updated_by: "j.ito", validation_pending: true }),
    );
    editor({ profile: profile({ validation_pending: true }) });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(screen.getByText(/changed to version 5 while you were editing \(by j\.ito\)/)).toBeInTheDocument();
  });

  it("renders a report with NO_EVENTS and QUERY_FAILED as saying nothing about the field", () => {
    const base = profile().validation!;
    editor({
      profile: profile({
        validation_status: "INCONCLUSIVE",
        validation: {
          ...base, status: "INCONCLUSIVE",
          fields: [
            { ...base.fields[0], status: "NO_EVENTS" },
            { ...base.fields[1], status: "QUERY_FAILED" },
          ],
        },
      }),
    });
    expect(screen.getAllByText(/says nothing about the field/)).toHaveLength(2);
  });
});

describe("ProfileEditor: action values", () => {
  it("saves a changed value map as a mapping change, from the values section", async () => {
    const save = vi.spyOn(api, "saveWafProfile").mockResolvedValue(profile({ profile_version: 5 }));
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    const section = screen.getByRole("region", { name: "Action values" });
    expect(within(section).queryByRole("button", { name: "Review and save" })).not.toBeInTheDocument();
    await user.click(within(within(section).getByRole("row", { name: /^ALERT\b/ })).getByRole("radio", { name: "Blocked" }));
    await user.click(within(section).getByRole("button", { name: "Review and save" }));
    const dialog = within(section).getByRole("region", { name: "Confirm the change" });
    expect(within(dialog).getByRole("list", { name: "Changed values" })).toHaveTextContent("Blocked values: + ALERT");
    expect(dialog).toHaveTextContent(/pauses until version 5 validates/);
    await user.click(within(dialog).getByRole("button", { name: "Save as version 5" }));
    expect(save).toHaveBeenCalledWith("ls-1", expect.objectContaining({
      action_value_map: { BLOCKED: ["BLOCK", "ALERT"], ALLOWED: ["ALLOW"] },
    }));
  });

  it("says a truncated list is truncated", () => {
    const base = profile().validation!;
    editor({
      profile: profile({
        validation: {
          ...base,
          fields: base.fields.map((f) => (f.field === "ACTION" ? { ...f, values_truncated: true } : f)),
        },
      }),
    });
    expect(screen.getByText(/this list is truncated/i)).toHaveTextContent(/never as allowed/);
  });

  it("explains values counted under an older property", () => {
    const base = profile().validation!;
    editor({
      profile: profile({
        mapping: { ...profile().mapping, ACTION: "Act2" },
        validation: { ...base, fields: base.fields.map((f) => (f.field === "ACTION" ? { ...f, property_name: "WAF_Action" } : f)) },
      }),
    });
    expect(screen.getByText(/counted under WAF_Action; the mapping now reads Act2/)).toBeInTheDocument();
  });

  it("blocks assigning values while the action property is unmapped", () => {
    editor({ profile: profile({ mapping: { ...profile().mapping, ACTION: null }, action_value_map: { BLOCKED: [], ALLOWED: [] } }) });
    expect(screen.getByText(/no property is mapped for action/i)).toBeInTheDocument();
  });
});

describe("ProfileEditor: collection", () => {
  it("disables Enable with the reason as text until the current version is valid", () => {
    editor({
      profile: profile({
        enabled: false, profile_version: 5, validation_status: "UNVALIDATED", validated_profile_version: null,
        collection_eligible: false, ineligible_reason: "version 5 is not validated",
      }),
    });
    const btn = screen.getByRole("button", { name: "Enable collection" });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/Version 5 has not been validated\. Validate it first\./)).toBeInTheDocument();
    expect(btn).toHaveAccessibleDescription(/has not been validated/);
  });

  it("enables a valid, disabled version", async () => {
    const enable = vi.spyOn(api, "enableWafProfile").mockResolvedValue(profile());
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor({ profile: profile({ enabled: false, collection_eligible: false, ineligible_reason: "disabled" }) });
    await user.click(screen.getByRole("button", { name: "Enable collection" }));
    expect(enable).toHaveBeenCalledWith("ls-1");
    expect(await screen.findByText("Collection is enabled for version 4.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable collection" })).toBeInTheDocument();
  });

  it("shows the server's reason when enabling is refused", async () => {
    vi.spyOn(api, "enableWafProfile").mockRejectedValue(
      new ApiError(409, "version 4 is not VALID; validate it first"),
    );
    const user = userEvent.setup();
    editor({ profile: profile({ enabled: false }) });
    await user.click(screen.getByRole("button", { name: "Enable collection" }));
    const alert = await screen.findByText(/^Not enabled\./);
    expect(alert).toHaveTextContent("version 4 is not VALID; validate it first");
    expect(screen.getByRole("button", { name: "Enable collection" })).toBeInTheDocument();
  });

  it("disables collection", async () => {
    const disable = vi.spyOn(api, "disableWafProfile").mockResolvedValue(
      profile({ enabled: false, collection_eligible: false, ineligible_reason: "disabled" }),
    );
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Disable collection" }));
    expect(disable).toHaveBeenCalledWith("ls-1");
    expect(await screen.findByText(/Collection is disabled\./)).toBeInTheDocument();
    expect(screen.getAllByText("Paused — disabled").length).toBeGreaterThan(0);
  });

  it("names the permission on a 403", async () => {
    vi.spyOn(api, "disableWafProfile").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Disable collection" }));
    expect(await screen.findByText(/admin:waf/)).toBeInTheDocument();
  });
});

describe("ProfileEditor: property catalog", () => {
  it("falls back to free text and says why when the catalog is unavailable", () => {
    editor({ catalog: catalog({ status: "UNAVAILABLE", properties: [], detail: "QRadar refused the request." }) });
    expect(screen.getByText(/property list is unavailable: QRadar refused the request\./)).toBeInTheDocument();
  });

  it("refreshes the list, bypassing the cache", async () => {
    const refresh = vi.spyOn(api, "wafPropertyCatalog").mockResolvedValue(catalog({ fetched_at: "2026-10-13T01:00:00Z" }));
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Refresh list" }));
    expect(refresh).toHaveBeenCalledWith("ls-1", true);
    expect(await screen.findByText(/Property list from this console, fetched/)).toHaveTextContent(
      "fetched 2026-10-13 01:00:00 UTC",
    );
  });

  it("keeps the previous list when a refresh fails, and says so", async () => {
    vi.spyOn(api, "wafPropertyCatalog").mockRejectedValue(new ApiError(502, "bad gateway"));
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Refresh list" }));
    expect(await screen.findByText(/could not be refreshed\. The previous list is still shown\./)).toBeInTheDocument();
    expect(screen.getByText(/Property list from this console/)).toBeInTheDocument();
  });

  it("warns about a name not in the catalog and about one for another type, without blocking a save", async () => {
    const save = vi.spyOn(api, "saveWafProfile").mockResolvedValue(profile({ profile_version: 5 }));
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor();
    await editUrl(user, "WAF_URL");
    expect(screen.getByText(/no parsing expression for this source’s log-source type \(Generic WAF DSM\)/)).toBeInTheDocument();
    await editUrl(user, "Nowhere");
    expect(screen.getByText(/Not in this console’s property list/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 5" }));
    expect(save).toHaveBeenCalledOnce();
  });
});

describe("ProfileEditor: copy from another source", () => {
  it("fills the form from the other source and saves nothing", async () => {
    const other = profile({
      log_source_id: "ls-2", log_source_name: "WAF edge - us", profile_version: 2,
      mapping: { URL: "request_uri", METHOD: "verb", VIRTUAL_HOST: "Host", ACTION: null, RESPONSE_CODE: null },
      action_value_map: { BLOCKED: [], ALLOWED: [] },
    });
    vi.spyOn(api, "wafProfile").mockResolvedValue(other);
    const save = vi.spyOn(api, "saveWafProfile");
    const user = userEvent.setup();
    editor({ siblings: [status({ log_source_id: "ls-2", log_source_name: "WAF edge - us", profile_version: 2 }), status({ log_source_id: "ls-9", log_source_name: "Other console", instance_id: "inst-9" })] });
    const select = screen.getByLabelText("Copy mapping from…");
    expect(within(select).queryByRole("option", { name: /Other console/ })).not.toBeInTheDocument();
    await user.selectOptions(select, "ls-2");
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText(/Copied from WAF edge - us \(version 2\)\. Nothing is saved until you save\./)).toBeInTheDocument();
    expect(input(/url path property/i)).toHaveValue("request_uri");
    expect(input(/virtual host property/i)).toHaveValue("Host");
    expect(screen.getByLabelText("Product label")).toHaveValue("Acme WAF");
    expect(save).not.toHaveBeenCalled();
  });
});

describe("ProfileEditor: history", () => {
  it("shows the history", () => {
    editor();
    expect(screen.getByRole("table", { name: /changes to this profile/i })).toBeInTheDocument();
  });
  it("says it is empty", () => {
    editor({ history: [] });
    expect(screen.getByText(/no changes are recorded/i)).toBeInTheDocument();
  });
  it("says a failed history read failed instead of calling it empty", () => {
    editor({ history: null });
    expect(screen.getByText(/history could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByText(/no changes are recorded/i)).not.toBeInTheDocument();
  });
});

describe("ProfileEditor: create mode", () => {
  const prefill = {
    draft: { ...draftFromProfile(SCHEMA, profile()), productLabel: "" },
    origin: "Prefilled from WAF edge - eu-west (version 4), the most recently validated profile on this console.",
  };

  it("offers to create, says where the prefill came from, and shows no validation yet", () => {
    editor({ profile: null, history: [], prefill });
    expect(screen.getByText("This log source has no WAF profile.")).toBeInTheDocument();
    expect(screen.getByText(/Prefilled from WAF edge - eu-west \(version 4\)/)).toBeInTheDocument();
    expect(input(/url path property/i)).toHaveValue("URL");
    expect(screen.getByText("No profile yet")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Validation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "History" })).not.toBeInTheDocument();
  });

  it("says when the prefill is the schema defaults", () => {
    editor({
      profile: null, history: [],
      prefill: { draft: { ...prefill.draft }, origin: "Prefilled from the schema defaults, because no validated profile on this console could seed it." },
    });
    expect(screen.getByText(/Prefilled from the schema defaults/)).toBeInTheDocument();
  });

  it("creates with no expected version, disabled, and says collection stays off", async () => {
    const created = profile({
      profile_version: 1, enabled: false, validation_status: "UNVALIDATED", validated_profile_version: null,
      validation: null, validation_pending: true, validation_requested_at: "2026-10-13T00:10:00Z",
      collection_eligible: false, ineligible_reason: "version 1 is not validated",
    });
    const save = vi.spyOn(api, "saveWafProfile").mockResolvedValue(created);
    vi.spyOn(api, "wafProfileHistory").mockResolvedValue(HISTORY);
    const user = userEvent.setup();
    editor({ profile: null, history: [], prefill });
    await user.click(screen.getByRole("button", { name: "Review and create" }));
    const dialog = screen.getByRole("region", { name: "Confirm the change" });
    expect(dialog).toHaveTextContent(/created disabled/i);
    expect(within(dialog).getByRole("list", { name: "Changed fields" })).toHaveTextContent("URL path: not mapped → URL");
    await user.click(within(dialog).getByRole("button", { name: "Create profile" }));
    expect(save).toHaveBeenCalledWith("ls-1", expect.objectContaining({ expected_profile_version: null }));
    expect(await screen.findByText(/Created version 1\. Collection stays off/)).toBeInTheDocument();
    expect(screen.getByText(/Validating version 1/)).toHaveTextContent("since 00:10 UTC");
    expect(screen.getByRole("heading", { name: "Validation" })).toBeInTheDocument();
    expect(screen.queryByText("This log source has no WAF profile.")).not.toBeInTheDocument();
  });

  it("will not create without the required fields", async () => {
    const save = vi.spyOn(api, "saveWafProfile");
    const user = userEvent.setup();
    editor({
      profile: null, history: [],
      prefill: { draft: { ...prefill.draft, mapping: { ...prefill.draft.mapping, URL: "" } }, origin: "Prefilled from the schema defaults." },
    });
    await user.click(screen.getByRole("button", { name: "Review and create" }));
    expect(screen.getByText(/collection cannot run without it/i)).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("reports a 409 on create (someone created it first)", async () => {
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(new ApiError(409, "Conflict", { current_version: 1, updated_by: null, pending_since: null, message: null }));
    const user = userEvent.setup();
    editor({ profile: null, history: [], prefill });
    await user.click(screen.getByRole("button", { name: "Review and create" }));
    await user.click(screen.getByRole("button", { name: "Create profile" }));
    expect(await screen.findByText(/changed to version 1 while you were editing/)).toBeInTheDocument();
  });
});

void waitFor;
