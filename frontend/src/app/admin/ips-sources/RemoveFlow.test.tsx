// UIQ-9: removing an IPS source from the list and from the editor, for a disabled profile that
// validated INCONCLUSIVE as well as a healthy one.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IpsProfileEditor } from "./IpsProfileEditor";
import { IpsSourceTable } from "./IpsSourceTable";
import { HISTORY, IPS_SCHEMA, catalog, panProfile, status } from "./fixtures";
import { fleetApi, ipsApi } from "@/lib/api";

const router = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  router.push.mockReset();
  router.refresh.mockReset();
  vi.spyOn(ipsApi, "sourceClass").mockResolvedValue({
    source_class: "IPS",
    basis: "RULE",
    override_class: null,
  });
});
afterEach(() => vi.restoreAllMocks());

const disabled = status({
  profile_id: "p-9",
  log_source_id: "ls-9",
  log_source_name: "PaSeries @ pa-fw-01",
  enabled: false,
  validation_status: "INCONCLUSIVE",
  validated_profile_version: null,
  validated_at: null,
  collection_eligible: false,
  ineligible_reason: "the profile is disabled",
});

describe("IPS sources table removal", () => {
  it("offers Remove on every row, a disabled INCONCLUSIVE one included", () => {
    render(<IpsSourceTable sources={[status(), disabled]} />);
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(2);
  });

  it("removes the row; a rule class is named, not cleared", async () => {
    const remove = vi.spyOn(ipsApi, "removeProfile").mockResolvedValue(undefined);
    const clear = vi.spyOn(fleetApi, "setOverride").mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(<IpsSourceTable sources={[status(), disabled]} />);
    await user.click(
      within(screen.getByRole("row", { name: /PaSeries/ })).getByRole("button", { name: "Remove" }),
    );
    expect(
      screen.getByRole("heading", { name: "Remove PaSeries @ pa-fw-01 from IPS?" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("The IPS class comes from a rule.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("ls-9"));
    expect(clear).not.toHaveBeenCalled();
    expect(screen.queryByRole("row", { name: /PaSeries/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Removed PaSeries @ pa-fw-01.");
  });
});

describe("IPS profile editor removal", () => {
  it("offers Remove from IPS and leaves for the list", async () => {
    const remove = vi.spyOn(ipsApi, "removeProfile").mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <IpsProfileEditor
        schema={IPS_SCHEMA}
        source={{ id: "ls-9", name: "PaSeries @ pa-fw-01", qradarId: 412, typeName: null }}
        profile={panProfile({ enabled: false, validation_status: "INCONCLUSIVE" })}
        catalog={catalog()}
        history={HISTORY}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Remove from IPS" }));
    await user.click(await screen.findByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("ls-9"));
    expect(router.push).toHaveBeenCalledWith("/admin/ips-sources");
  });
});
