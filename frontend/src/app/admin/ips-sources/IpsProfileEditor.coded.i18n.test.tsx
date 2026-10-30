// MSG-1b: the IPS profile editor words a coded conflict or input refusal in the reader's language;
// English shows the server's text as sent.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../../vitest.setup";
import { IpsProfileEditor, type IpsEditorSource } from "./IpsProfileEditor";
import { HISTORY, IPS_SCHEMA, catalog, profile } from "./fixtures";
import { ApiError, ipsApi } from "@/lib/api";

const SOURCE: IpsEditorSource = {
  id: "ls-413",
  name: "CiscoFirepowerThreatDefense @ ftd-01",
  qradarId: 413,
  typeName: "Cisco Firepower Threat Defense",
};

function editor() {
  return render(
    <IpsProfileEditor
      schema={IPS_SCHEMA}
      source={SOURCE}
      profile={profile()}
      catalog={catalog()}
      history={HISTORY}
    />,
  );
}

const refused = (status: number, detail: Record<string, unknown>) =>
  new ApiError(status, "refused", detail, { detail });

beforeEach(() => vi.restoreAllMocks());

async function editSignature(user: ReturnType<typeof userEvent.setup>) {
  const group = screen.getAllByRole("group").find((item) => /signature|imza/i.test(item.getAttribute("aria-label") ?? item.textContent ?? ""));
  if (!group) throw new Error("no signature group");
  await user.click(within(group).getAllByRole("radio")[1]);
  await user.type(screen.getByRole("combobox", { name: /signature|imza/i }), "PAN_ThreatID");
}

describe("IPS profile editor: coded refusals", () => {
  it("words a stale-version conflict in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      refused(409, {
        message: "profile version conflict; current version is 3",
        code: "validation.profile.conflict.version",
        params: { current: 3 },
        current_version: 3,
        updated_by: "j.ito",
        pending_since: null,
      }),
    );
    const user = userEvent.setup();
    editor();
    await editSignature(user);
    await user.click(screen.getByRole("button", { name: "Gözden geçir ve kaydet" }));
    await user.click(screen.getByRole("button", { name: "Sürüm 3 olarak kaydet" }));
    expect(await screen.findByText(/Profil sürümü çakışması; geçerli sürüm 3\./)).toBeInTheDocument();
    expect(screen.queryByText(/profile version conflict/)).toBeNull();
  });

  it("words an input refusal in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      refused(422, {
        message: "an action value is mapped to both BLOCKED and ALLOWED",
        code: "validation.ips.input.action_overlap",
        params: {},
      }),
    );
    const user = userEvent.setup();
    editor();
    await editSignature(user);
    await user.click(screen.getByRole("button", { name: "Gözden geçir ve kaydet" }));
    await user.click(screen.getByRole("button", { name: "Sürüm 3 olarak kaydet" }));
    expect(
      await screen.findAllByText("Bir eylem değeri hem BLOCKED hem ALLOWED olarak eşlenmiş"),
    ).not.toHaveLength(0);
    expect(screen.queryByText(/mapped to both/)).toBeNull();
  });

  it("shows the server's English in English", async () => {
    setTestLocale("en");
    vi.spyOn(ipsApi, "saveProfile").mockRejectedValue(
      refused(422, {
        message: "an action value is mapped to both BLOCKED and ALLOWED",
        code: "validation.ips.input.action_overlap",
        params: {},
      }),
    );
    const user = userEvent.setup();
    editor();
    await editSignature(user);
    await user.click(screen.getByRole("button", { name: "Review and save" }));
    await user.click(screen.getByRole("button", { name: "Save as version 3" }));
    expect(
      await screen.findAllByText("an action value is mapped to both BLOCKED and ALLOWED"),
    ).not.toHaveLength(0);
  });

  it("words an enable refusal in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(ipsApi, "enableProfile").mockRejectedValue(
      refused(409, {
        message: "profile version 2 has not validated (status UNVALIDATED); run validation first",
        code: "validation.profile.conflict.not_validated",
        params: { profile_version: 2, validation_status: "UNVALIDATED" },
      }),
    );
    const user = userEvent.setup();
    render(
      <IpsProfileEditor
        schema={IPS_SCHEMA}
        source={SOURCE}
        profile={profile({ enabled: false, collection_eligible: false })}
        catalog={catalog()}
        history={HISTORY}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Toplamayı etkinleştir" }));
    const alert = await screen.findByText(/^Etkinleştirilmedi\./);
    expect(alert).toHaveTextContent("Profil sürümü 2 doğrulanmadı");
    expect(alert).not.toHaveTextContent("UNVALIDATED");
  });
});
