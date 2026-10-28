import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { SearchEditor } from "./SearchEditor";
import { SearchEnableControl } from "./SearchEnableControl";
import { ApiError, api } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

const REASONS = [
  "disallowed keyword(s): DELETE",
  "LIMIT 20000 exceeds the maximum 10000 rows",
];
const CODES = [
  { code: "search.aql.disallowed_keywords", params: { keywords: "DELETE" } },
  { code: "search.aql.limit_exceeded", params: { limit: 20000, max_rows: 10000 } },
];
const unsafe = () => {
  const detail = { message: `unsafe AQL: ${REASONS.join("; ")}`, reasons: REASONS, reason_codes: CODES };
  return new ApiError(422, "Unprocessable Entity", detail, { detail });
};
const capped = (limit: number) => {
  const detail = `the limit is ${limit} enabled searches`;
  return new ApiError(409, detail, detail, {
    detail,
    detail_code: "search.enabled_cap",
    detail_params: { limit },
  });
};

beforeEach(() => vi.restoreAllMocks());

async function submitNew(user: ReturnType<typeof userEvent.setup>, name: string, aql: string, save: string) {
  await user.type(screen.getByRole("textbox", { name }), "Nightly count");
  await user.type(screen.getByRole("textbox", { name: aql }), "DELETE FROM events");
  await user.click(screen.getByRole("button", { name: save }));
}

describe("search editor: coded authoring reasons", () => {
  it("lists the unsafe-AQL reasons in Turkish with grouped counts", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "createSearch").mockRejectedValue(unsafe());
    const user = userEvent.setup();
    render(<SearchEditor mode="create" />);
    await submitNew(user, "Ad", "AQL", "Devre dışı olarak kaydet");
    const list = await screen.findByLabelText("Doğrulama nedenleri");
    expect(Array.from(list.querySelectorAll("li")).map((item) => item.textContent)).toEqual([
      "İzin verilmeyen anahtar sözcükler: DELETE",
      "LIMIT 20.000, en fazla 10.000 satırlık üst sınırı aşıyor",
    ]);
  });

  it("lists the server's English in English", async () => {
    setTestLocale("en");
    vi.spyOn(api, "createSearch").mockRejectedValue(unsafe());
    const user = userEvent.setup();
    render(<SearchEditor mode="create" />);
    await submitNew(user, "Name", "AQL", "Save as disabled");
    const list = await screen.findByLabelText("Validation reasons");
    expect(Array.from(list.querySelectorAll("li")).map((item) => item.textContent)).toEqual(REASONS);
  });

  it("words the enabled-search cap in Turkish on the editor and on the enable control", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "createSearch").mockRejectedValue(capped(1204));
    const user = userEvent.setup();
    const editor = render(<SearchEditor mode="create" />);
    await submitNew(user, "Ad", "AQL", "Devre dışı olarak kaydet");
    expect(await screen.findByText(/Etkinleştirilmedi: Etkin arama sınırı 1\.204\./)).toBeInTheDocument();
    editor.unmount();

    vi.spyOn(api, "setSearchEnabled").mockRejectedValue(capped(50));
    render(<SearchEnableControl id="s-1" enabled={false} />);
    await user.click(screen.getByRole("button", { name: "Aramayı etkinleştir" }));
    expect(await screen.findByText(/Etkinleştirilmedi: Etkin arama sınırı 50\./)).toBeInTheDocument();
  });

  it("keeps the English cap sentence in English", async () => {
    setTestLocale("en");
    vi.spyOn(api, "setSearchEnabled").mockRejectedValue(capped(50));
    const user = userEvent.setup();
    render(<SearchEnableControl id="s-1" enabled={false} />);
    await user.click(screen.getByRole("button", { name: "Enable search" }));
    expect(await screen.findByText(/Not enabled: the limit is 50 enabled searches\./)).toBeInTheDocument();
  });
});
