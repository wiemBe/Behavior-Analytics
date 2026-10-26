import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { ApiError, api } from "@/lib/api";
import { CustomRuleEditor } from "./CustomRuleEditor";
import { catalog, preview, rule } from "./fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("./SubjectPicker", () => ({ SubjectPicker: () => <div aria-label="özne seçici" /> }));
vi.mock("./PreviewChart", () => ({
  PreviewChart: () => <div aria-label="önizleme grafiği" />,
  PreviewSummary: () => <p>Önizleme özeti</p>,
}));

beforeEach(() => {
  setTestLocale("tr");
  vi.spyOn(api, "previewCustomRule").mockResolvedValue(preview());
});

describe("custom-rule editor in Turkish", () => {
  it("labels the fields and reports the first frontend validation exit", () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    expect(screen.getByRole("textbox", { name: "Ad" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "YÜKSEK" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Kural oluştur" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Ad zorunludur.");
  });

  it("keeps backend validation reasons verbatim inside translated chrome", async () => {
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", { reasons: ["backend reason"] }),
    );
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Değişiklikleri kaydet" }));
    expect(await screen.findByText("backend reason")).toBeInTheDocument();
    expect(screen.getByLabelText("Doğrulama nedenleri")).toBeInTheDocument();
  });
});
