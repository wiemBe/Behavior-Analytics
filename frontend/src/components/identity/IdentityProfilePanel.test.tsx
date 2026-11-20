import { act, render, screen, waitFor } from "@testing-library/react";
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
    expect(screen.getByText("Validate the current version to enable.")).toBeInTheDocument();
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

const queued = (over = {}) =>
  profile({
    validation_pending: true,
    validation_requested_at: "2026-11-20T10:00:00Z",
    validation_requested_by: "analyst",
    ...over,
  });

const detail = (profileOver = {}) => ({
  profile: profile(profileOver),
  series: [],
});

describe("IdentityProfilePanel: queued validation", () => {
  it("shows the queued state with its UTC time after the 202, and disables Validate", async () => {
    const validate = vi.spyOn(identityApi, "validate").mockResolvedValue(queued());
    vi.spyOn(identityApi, "source").mockReturnValue(new Promise(() => {}));
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(validate).toHaveBeenCalledWith(SOURCE_ID);
    expect(await screen.findByText(/Queued at/)).toBeInTheDocument();
    expect(screen.getByText(/2026-11-20 10:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validation queued" })).toBeDisabled();
    expect(screen.queryByText("Validation finished.")).toBeNull();
  });

  it("polls a pending profile and shows the stored outcome when it clears", async () => {
    // The read that would replace the queued frame returns a promise this test
    // resolves only after it has seen that frame: the panel polls on a 10 ms
    // timer, so a resolved read could otherwise replace the queued state with
    // the finished one before `findByText` (50 ms re-check) ever observes it.
    let resolveFinished!: (value: unknown) => void;
    const finished = new Promise((resolve) => {
      resolveFinished = resolve;
    });
    const source = vi
      .spyOn(identityApi, "source")
      .mockResolvedValueOnce(detail({ ...queued() }) as never)
      .mockReturnValueOnce(finished as never);
    render(
      <IdentityProfilePanel logSourceId={SOURCE_ID} profile={queued()} canAdmin pollMs={10} />,
    );
    expect(screen.getByText(/Queued at/)).toBeInTheDocument();
    await act(async () => {
      resolveFinished(detail({ validation_status: "VALID" }));
    });
    expect(await screen.findByText("Validation finished.")).toBeInTheDocument();
    expect(screen.getByText("Valid")).toBeInTheDocument();
    expect(screen.queryByText(/Queued at/)).toBeNull();
    const calls = source.mock.calls.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(source.mock.calls.length).toBe(calls);
  });

  it("stops polling when the panel unmounts", async () => {
    const source = vi.spyOn(identityApi, "source").mockResolvedValue(detail(queued() as never) as never);
    const view = render(
      <IdentityProfilePanel logSourceId={SOURCE_ID} profile={queued()} canAdmin pollMs={10} />,
    );
    await waitFor(() => expect(source).toHaveBeenCalled());
    view.unmount();
    const calls = source.mock.calls.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(source.mock.calls.length).toBe(calls);
  });

  it("does not poll an idle profile", async () => {
    const source = vi.spyOn(identityApi, "source");
    render(<IdentityProfilePanel logSourceId={SOURCE_ID} profile={profile()} canAdmin pollMs={10} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(source).not.toHaveBeenCalled();
  });

  it("keeps the last state and keeps polling when one poll fails", async () => {
    vi.spyOn(identityApi, "source")
      .mockRejectedValueOnce(new ApiError(502, "bad gateway"))
      .mockResolvedValue(detail({ validation_status: "VALID" }) as never);
    render(
      <IdentityProfilePanel logSourceId={SOURCE_ID} profile={queued()} canAdmin pollMs={10} />,
    );
    expect(await screen.findByText("Validation finished.")).toBeInTheDocument();
  });

  it("says why an invalid result is invalid", () => {
    panel(
      profile({
        validation_status: "INVALID",
        validation_report: { event_id_rejected: false, catalog_overlap: 0, total_events: 900 },
      }),
    );
    expect(screen.getByText("Invalid")).toBeInTheDocument();
    expect(
      screen.getByText("None of the enabled event IDs appeared in the probe window."),
    ).toBeInTheDocument();
  });

  it("says a refused property is the reason, without naming it", () => {
    panel(
      profile({
        validation_status: "INVALID",
        validation_report: { username_rejected: true },
      }),
    );
    expect(screen.getByText(/QRadar refused a mapped property/)).toBeInTheDocument();
  });

  it("says too few populated events when the ratio is low", () => {
    panel(
      profile({
        validation_status: "INVALID",
        validation_report: { catalog_overlap: 2, total_events: 900 },
      }),
    );
    expect(screen.getByText(/Too few events carry the mapped event ID/)).toBeInTheDocument();
  });

  it("says an inconclusive probe could not judge the mapping", () => {
    panel(
      profile({
        validation_status: "INCONCLUSIVE",
        validation_report: { total_events: 0 },
      }),
    );
    expect(screen.getByText("Inconclusive")).toBeInTheDocument();
    expect(screen.getByText(/held no events/)).toBeInTheDocument();
  });

  it("says the probe did not finish, with the stored reason, and keeps the old status", () => {
    panel(
      profile({
        validation_status: "UNVALIDATED",
        validated_profile_version: null,
        validated_at: null,
        validation_report: { validation_error: "provider_error" },
      }),
    );
    expect(screen.getAllByText("Not validated").length).toBeGreaterThan(0);
    expect(
      screen.getByText("The probe did not finish (provider_error). The stored status is unchanged."),
    ).toBeInTheDocument();
  });

  it("shows nothing about a reason for a valid profile", () => {
    panel();
    expect(screen.queryByText(/probe window/)).toBeNull();
    expect(screen.queryByText(/did not finish/)).toBeNull();
  });

  it("shows the already-running refusal and follows the queued validation", async () => {
    vi.spyOn(identityApi, "validate").mockRejectedValue(
      new ApiError(409, "validation of version 2 is already running since 2026-11-20T10:00:00+00:00", {
        message: "validation of version 2 is already running since 2026-11-20T10:00:00+00:00",
        code: "validation.profile.conflict.running",
        params: { profile_version: "2", since: "2026-11-20T10:00:00+00:00" },
      }),
    );
    vi.spyOn(identityApi, "source").mockResolvedValue(detail(queued() as never) as never);
    panel();
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already running since/);
    expect(await screen.findByText(/Queued at/)).toBeInTheDocument();
  });
});
