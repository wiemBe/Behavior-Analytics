// MSG-1b: the WAF profile editor words a coded conflict or input refusal in the reader's language.
// English shows the server's text as sent; the field an input refusal concerns is found from the
// server's English either way.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../../vitest.setup";
import { ProfileEditor, type EditorSource } from "./ProfileEditor";
import { HISTORY, SCHEMA, catalog, profile } from "@/components/fieldmapping/fixtures";
import { ApiError, api } from "@/lib/api";

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

const refused = (status: number, detail: Record<string, unknown>) =>
  new ApiError(status, "refused", detail, { detail });

beforeEach(() => vi.restoreAllMocks());

async function saveAfterEditing(user: ReturnType<typeof userEvent.setup>, review: string, save: string) {
  const field = screen.getByRole("combobox", { name: /url path (property|özelliği)/i });
  await user.clear(field);
  await user.type(field, "request_uri");
  await user.click(screen.getByRole("button", { name: review }));
  await user.click(screen.getByRole("button", { name: save }));
}

describe("WAF profile editor: coded refusals", () => {
  it("words a stale-version conflict in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      refused(409, {
        message: "profile version conflict; current version is 7",
        code: "validation.profile.conflict.version",
        params: { current: 7 },
        current_version: 7,
        updated_by: "k",
        pending_since: null,
      }),
    );
    const user = userEvent.setup();
    editor();
    await saveAfterEditing(user, "Gözden geçir ve kaydet", "Sürüm 5 olarak kaydet");
    expect(await screen.findByText(/Profil sürümü çakışması; geçerli sürüm 7/)).toBeInTheDocument();
    expect(screen.queryByText(/profile version conflict/)).toBeNull();
  });

  it("keeps the English conflict as the server sent it", async () => {
    setTestLocale("en");
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      refused(409, {
        message: "profile version conflict; current version is 7",
        code: "validation.profile.conflict.version",
        params: { current: 7 },
        current_version: 7,
        updated_by: "k",
        pending_since: null,
      }),
    );
    const user = userEvent.setup();
    editor();
    await saveAfterEditing(user, "Review and save", "Save as version 5");
    expect(await screen.findByText(/profile version conflict; current version is 7/)).toBeInTheDocument();
  });

  it("words an input refusal in Turkish and still marks the field it names", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "saveWafProfile").mockRejectedValue(
      refused(422, {
        message:
          "invalid QRadar property name 'request_uri': letters, digits, underscore, space and hyphen only, at most 64 characters",
        code: "validation.waf.input.property_name",
        params: { name: "'request_uri'" },
      }),
    );
    const user = userEvent.setup();
    editor();
    await saveAfterEditing(user, "Gözden geçir ve kaydet", "Sürüm 5 olarak kaydet");
    expect(
      await screen.findByText(/'request_uri' geçerli bir QRadar özellik adı değil/),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /URL path özelliği/i })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("words a validation that is already running in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "validateWafProfile").mockRejectedValue(
      refused(409, {
        message: "validation of version 4 is already running since 2026-10-28T10:00:00+00:00",
        code: "validation.profile.conflict.running",
        params: { profile_version: 4, since: "2026-10-28T10:00:00+00:00" },
        current_version: 4,
        updated_by: null,
        pending_since: null,
      }),
    );
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Yeniden doğrula" }));
    expect(
      await screen.findByText(
        /Doğrulama başlatılmadı\. 4 sürümünün doğrulaması 2026-10-28T10:00:00\+00:00 tarihinden beri zaten çalışıyor/,
      ),
    ).toBeInTheDocument();
  });

  it("words an enable refusal in Turkish with the status label, not the code", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "enableWafProfile").mockRejectedValue(
      refused(409, {
        message: "profile version 4 has not validated (status INVALID); run validation first",
        code: "validation.profile.conflict.not_validated",
        params: { profile_version: 4, validation_status: "INVALID" },
      }),
    );
    const user = userEvent.setup();
    editor({ profile: profile({ enabled: false }) });
    await user.click(screen.getByRole("button", { name: "Toplamayı etkinleştir" }));
    const alert = await screen.findByText(/^Etkinleştirilmedi\./);
    expect(alert).toHaveTextContent("Profil sürümü 4 doğrulanmadı (durum: Geçersiz); önce doğrulamayı çalıştırın");
    expect(alert).not.toHaveTextContent("INVALID");
  });

  it("shows an uncoded refusal as the server sent it", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "validateWafProfile").mockRejectedValue(
      new ApiError(409, "validation of version 4 is already running since 23:52 UTC"),
    );
    const user = userEvent.setup();
    editor();
    await user.click(screen.getByRole("button", { name: "Yeniden doğrula" }));
    expect(await screen.findByText(/already running since 23:52 UTC/)).toBeInTheDocument();
  });
});
