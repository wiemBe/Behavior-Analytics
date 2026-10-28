import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { ApiError, api } from "@/lib/api";
import { CustomRuleEditor } from "./CustomRuleEditor";
import { catalog, preview, rule } from "./fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("./SubjectPicker", () => ({ SubjectPicker: () => <div aria-label="subject picker" /> }));
vi.mock("./PreviewChart", () => ({
  PreviewChart: () => <div aria-label="preview chart" />,
  PreviewSummary: () => <p>preview summary</p>,
}));

const REASONS = [
  "metric \"it's\" is not in the custom-rule catalog",
  "sustain_buckets must be at least 1",
];
const CODES = [
  { code: "ruledraft.metric_unknown", params: { metric: "\"it's\"" } },
  { code: "ruledraft.sustain_min", params: {} },
];
const unprocessable = () =>
  new ApiError(
    422,
    "Unprocessable Entity",
    { message: REASONS.join("; "), reasons: REASONS, reason_codes: CODES },
    { detail: { message: REASONS.join("; "), reasons: REASONS, reason_codes: CODES } },
  );

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "previewCustomRule").mockResolvedValue(preview());
});

describe("custom-rule editor: coded validation reasons", () => {
  it("lists each reason in Turkish, a quoted value intact", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(unprocessable());
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Değişiklikleri kaydet" }));
    const list = await screen.findByLabelText("Doğrulama nedenleri");
    const items = Array.from(list.querySelectorAll("li")).map((item) => item.textContent);
    expect(items).toEqual([
      "\"it's\" metriği özel kural kataloğunda yok",
      "sustain_buckets en az 1 olmalıdır",
    ]);
  });

  it("lists the server's English in English", async () => {
    setTestLocale("en");
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(unprocessable());
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    const list = await screen.findByLabelText("Validation reasons");
    expect(Array.from(list.querySelectorAll("li")).map((item) => item.textContent)).toEqual(REASONS);
  });

  it("words the enabled-rule cap in Turkish with the configured limit", async () => {
    setTestLocale("tr");
    const detail = "at most 1204 rules may be enabled at once (1204 already are)";
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(
      new ApiError(409, detail, detail, {
        detail,
        detail_code: "ruledraft.enabled_cap",
        detail_params: { max_enabled: 1204, enabled_count: 1204 },
      }),
    );
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Değişiklikleri kaydet" }));
    expect(await screen.findByText(/Aynı anda en fazla 1\.204 kural etkin olabilir \(şu an 1\.204 etkin\)/))
      .toBeInTheDocument();
  });
});
