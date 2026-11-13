import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, identityApi } from "@/lib/api";

import { SOURCE_ID, profile } from "./fixtures";
import { IdentityProfilePanel } from "./IdentityProfilePanel";

beforeEach(() => {
  vi.restoreAllMocks();
});

const panel = (p = profile(), canAdmin = true) =>
  render(<IdentityProfilePanel logSourceId={SOURCE_ID} profile={p} canAdmin={canAdmin} />);

describe("IdentityProfilePanel: read", () => {
  it("shows the mapping and the validation of the current version", () => {
    panel();
    expect(screen.getByText("WinEventID")).toBeInTheDocument();
    expect(screen.getByText("Valid")).toBeInTheDocument();
    expect(screen.getByText("Validated for version 2; current version 2")).toBeInTheDocument();
    expect(screen.getByText("Not mapped")).toBeInTheDocument();
  });

  it("does not call a profile validated for an older version collected", () => {
    panel(profile({ validated_profile_version: 1 }));
    expect(screen.getAllByText("Validation out of date").length).toBeGreaterThan(0);
    expect(screen.queryByText("Collected")).toBeNull();
  });

  it("offers no control to a reader, and names the grant", () => {
    panel(profile(), false);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/needs the admin:identity permission/)).toBeInTheDocument();
  });
});

describe("IdentityProfilePanel: write", () => {
  it("validates, then reflects the new profile", async () => {
    const validate = vi
      .spyOn(identityApi, "validate")
      .mockResolvedValue(profile({ validation_status: "INVALID" }));
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(validate).toHaveBeenCalledWith(SOURCE_ID);
    expect(await screen.findByText("Validation finished.")).toBeInTheDocument();
    expect(screen.getByText("Invalid")).toBeInTheDocument();
  });

  it("disables an enabled source and offers Enable afterwards", async () => {
    const disable = vi.spyOn(identityApi, "disable").mockResolvedValue(profile({ enabled: false }));
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Disable" }));
    expect(disable).toHaveBeenCalledWith(SOURCE_ID);
    expect(await screen.findByRole("button", { name: "Enable" })).toBeEnabled();
  });

  it("will not offer Enable while the current version is not validated", () => {
    panel(profile({ enabled: false, validated_profile_version: null, validation_status: "UNVALIDATED" }));
    expect(screen.getByRole("button", { name: "Enable" })).toBeDisabled();
    expect(screen.getByText("Enable is available once the current version is validated.")).toBeInTheDocument();
  });

  it("enables a validated source", async () => {
    const enable = vi.spyOn(identityApi, "enable").mockResolvedValue(profile());
    panel(profile({ enabled: false }));
    await userEvent.click(screen.getByRole("button", { name: "Enable" }));
    expect(enable).toHaveBeenCalledWith(SOURCE_ID);
    expect(await screen.findByText("Collection enabled.")).toBeInTheDocument();
  });

  it("shows the server's refusal sentence when enable is refused", async () => {
    vi.spyOn(identityApi, "enable").mockRejectedValue(
      new ApiError(409, "Profile version 3 is not validated.", {
        message: "Profile version 3 is not validated.",
        code: "identity.profile.enable_refused",
        params: { version: "3" },
      }),
    );
    panel(profile({ enabled: false }));
    await userEvent.click(screen.getByRole("button", { name: "Enable" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/version 3/);
  });

  it("names admin:identity on a 403", async () => {
    vi.spyOn(identityApi, "validate").mockRejectedValue(
      new ApiError(403, "forbidden", "missing required permission: admin:identity"),
    );
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("admin:identity");
  });

  it("says what failed on a server error", async () => {
    vi.spyOn(identityApi, "validate").mockRejectedValue(new ApiError(500, "boom"));
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Not saved/);
  });

  it("sets a mapping with only the catalogue events and a trimmed, optional logon type", async () => {
    const save = vi.spyOn(identityApi, "saveProfile").mockResolvedValue(profile({ profile_version: 3 }));
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Edit mapping" }));
    await userEvent.clear(screen.getByLabelText("Event ID property"));
    await userEvent.type(screen.getByLabelText("Event ID property"), "  EventCode ");
    await userEvent.click(screen.getByRole("checkbox", { name: /1102/ }));
    await userEvent.click(screen.getByRole("button", { name: "Set mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    const body = save.mock.calls[0][1];
    expect(body.event_id_property).toBe("EventCode");
    expect(body.logon_type_property).toBeNull();
    expect(body.enabled_event_ids).toEqual([4625, 4740, 4720, 4726, 4728, 4732, 4756]);
    expect(await screen.findByText("Mapping saved. Validate it before enabling.")).toBeInTheDocument();
  });

  it("will not send a mapping with no event property or no event", async () => {
    const save = vi.spyOn(identityApi, "saveProfile");
    panel(profile({ enabled_event_ids: [4625] }));
    await userEvent.click(screen.getByRole("button", { name: "Edit mapping" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /4625/ }));
    expect(screen.getByRole("button", { name: "Set mapping" })).toBeDisabled();
    expect(save).not.toHaveBeenCalled();
  });

  it("creates a profile for a source that has none", async () => {
    const save = vi.spyOn(identityApi, "saveProfile").mockResolvedValue(profile());
    panel(null as never);
    expect(screen.getByText("This log source has no identity profile.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Event ID property"), "WinEventID");
    await userEvent.click(screen.getByRole("button", { name: "Set mapping" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
  });
});
