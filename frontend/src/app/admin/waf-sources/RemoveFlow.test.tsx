// UIQ-9: removing a WAF source from the list and from the editor, for a disabled profile that
// validated INCONCLUSIVE (the production case) as well as a healthy one.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileEditor } from "./ProfileEditor";
import { WafSourceTable } from "./WafSourceTable";
import {
  HISTORY,
  SCHEMA,
  catalog,
  profile,
  status,
} from "@/components/fieldmapping/fixtures";
import { api, fleetApi } from "@/lib/api";
import { draftFromDefaults } from "@/lib/fieldMapping";

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
  vi.spyOn(api, "wafSourceClass").mockResolvedValue({
    source_class: "WAF",
    basis: "OVERRIDE",
    override_class: "WAF",
  });
});
afterEach(() => vi.restoreAllMocks());

const disabled = status({
  profile_id: "p-2",
  log_source_id: "ls-2",
  log_source_name: "f51waf_upt-1",
  enabled: false,
  validation_status: "INCONCLUSIVE",
  validated_profile_version: null,
  validated_at: null,
  collection_eligible: false,
  ineligible_reason: "the profile is disabled",
});

describe("WAF sources table removal", () => {
  it("offers Remove on every row, a disabled INCONCLUSIVE one included", () => {
    render(<WafSourceTable sources={[status(), disabled]} schema={SCHEMA} />);
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(2);
    const row = screen.getByRole("row", { name: /f51waf_upt-1/ });
    expect(within(row).getByRole("button", { name: "Remove" })).toBeEnabled();
  });

  it("removes the row, says so, clears the override and refreshes the list", async () => {
    const remove = vi.spyOn(api, "removeWafProfile").mockResolvedValue(undefined);
    const clear = vi.spyOn(fleetApi, "setOverride").mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(<WafSourceTable sources={[status(), disabled]} schema={SCHEMA} />);
    await user.click(
      within(screen.getByRole("row", { name: /f51waf_upt-1/ })).getByRole("button", {
        name: "Remove",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Remove f51waf_upt-1 from WAF?" }),
    ).toBeInTheDocument();
    await screen.findByRole("checkbox", { name: "Also clear the WAF class" });
    await user.click(screen.getByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("ls-2"));
    expect(clear).toHaveBeenCalledWith("ls-2", null);
    expect(screen.queryByRole("row", { name: /f51waf_upt-1/ })).not.toBeInTheDocument();
    expect(screen.getByRole("row", { name: /WAF edge/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Removed f51waf_upt-1.");
  });

  it("removing the last row leaves the note and no empty table", async () => {
    vi.spyOn(api, "removeWafProfile").mockResolvedValue(undefined);
    vi.spyOn(fleetApi, "setOverride").mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(<WafSourceTable sources={[disabled]} schema={SCHEMA} />);
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await user.click(await screen.findByRole("button", { name: "Remove source" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Removed f51waf_upt-1.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("keeps the row when the removal fails", async () => {
    vi.spyOn(api, "removeWafProfile").mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    render(<WafSourceTable sources={[disabled]} schema={SCHEMA} />);
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await user.click(await screen.findByRole("button", { name: "Remove source" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not remove the WAF source. Try again.",
    );
    expect(screen.getByRole("row", { name: /f51waf_upt-1/ })).toBeInTheDocument();
  });
});

describe("WAF profile editor removal", () => {
  it("offers Remove from WAF on a disabled INCONCLUSIVE profile and leaves for the list", async () => {
    const remove = vi.spyOn(api, "removeWafProfile").mockResolvedValue(undefined);
    vi.spyOn(fleetApi, "setOverride").mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(
      <ProfileEditor
        schema={SCHEMA}
        source={{ id: "ls-2", name: "f51waf_upt-1", qradarId: 212, typeName: null, instanceId: "i" }}
        profile={profile({
          log_source_id: "ls-2",
          enabled: false,
          validation_status: "INCONCLUSIVE",
          validated_profile_version: null,
          validated_at: null,
          collection_eligible: false,
        })}
        catalog={catalog()}
        history={HISTORY}
        siblings={[]}
        prefill={null}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Remove from WAF" }));
    await user.click(await screen.findByRole("button", { name: "Remove source" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("ls-2"));
    expect(router.push).toHaveBeenCalledWith("/admin/waf-sources");
  });

  it("has no Remove in create mode, where there is no profile", () => {
    render(
      <ProfileEditor
        schema={SCHEMA}
        source={{ id: "ls-3", name: "new", qradarId: 3, typeName: null, instanceId: "i" }}
        profile={null}
        catalog={catalog()}
        history={[]}
        siblings={[]}
        prefill={{ draft: draftFromDefaults(SCHEMA), origin: { kind: "defaults" } }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Remove from WAF" })).not.toBeInTheDocument();
  });
});
