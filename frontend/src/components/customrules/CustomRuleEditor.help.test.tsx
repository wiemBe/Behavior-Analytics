import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { api } from "@/lib/api";
import { CustomRuleEditor } from "./CustomRuleEditor";
import { catalog, preview } from "./fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("./SubjectPicker", () => ({ SubjectPicker: () => <div aria-label="subject picker" /> }));
vi.mock("./PreviewChart", () => ({
  PreviewChart: () => <div aria-label="preview chart" />,
  PreviewSummary: () => <p>summary</p>,
}));

beforeEach(() => {
  setTestLocale("en");
  vi.spyOn(api, "previewCustomRule").mockResolvedValue(preview());
});

const EN = [
  ["Metric", "Metric"],
  ["Operator", "Operator"],
  ["Threshold", /^Threshold/],
  ["Sustain", /^Sustain/],
  ["Recovery", /^Recovery/],
  ["Window", "Window"],
  ["Severity", "Severity"],
] as const;

describe("custom-rule editor field help", () => {
  it("words the operator options and keeps the code as the value", () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    const operator = screen.getByLabelText("Operator") as HTMLSelectElement;
    const options = within(operator).getAllByRole("option").map((o) => [o.getAttribute("value"), o.textContent]);
    expect(options).toEqual([
      ["EQ", "equal to"],
      ["GE", "at or above"],
      ["GT", "above"],
      ["LE", "at or below"],
      ["LT", "below"],
    ]);
    expect(screen.queryByRole("option", { name: "GT" })).toBeNull();
  });

  it("offers a named help button for every technical field", () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    for (const [name] of EN) {
      expect(screen.getByRole("button", { name: `About ${name}` })).toHaveAttribute("type", "button");
    }
  });

  it.each(EN)("%s: opens on focus, links the field while shown, closes on Escape", async (name, label) => {
    const user = userEvent.setup();
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    const field = screen.getByLabelText(label);
    expect(field).not.toHaveAttribute("aria-describedby");
    const button = screen.getByRole("button", { name: `About ${name}` });
    button.focus();
    const tip = await screen.findByRole("tooltip");
    expect(field).toHaveAttribute("aria-describedby", tip.id);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(field).not.toHaveAttribute("aria-describedby");
  });

  it("opens on hover and on click", async () => {
    const user = userEvent.setup();
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    const button = screen.getByRole("button", { name: "About Sustain" });
    await user.hover(button);
    expect(screen.getByRole("tooltip")).toHaveTextContent("How many breaching slices in a row");
    await user.unhover(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.click(button);
    expect(screen.getByRole("tooltip")).toBeVisible();
  });

  it("says in the metric text that an unmeasured slice is not a zero", async () => {
    const user = userEvent.setup();
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    await user.hover(screen.getByRole("button", { name: "About Metric" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("is not a zero");
  });

  it("takes the window bounds from the catalog", async () => {
    const user = userEvent.setup();
    render(<CustomRuleEditor catalog={catalog({ preview_hours_min: 2, preview_hours_max: 96 })} mode="create" />);
    await user.hover(screen.getByRole("button", { name: "About Window" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("from 2 to 96");
  });
});

describe("custom-rule editor field help in Turkish", () => {
  beforeEach(() => setTestLocale("tr"));

  it("words the operators and names the help buttons", () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    const operator = screen.getByLabelText("İşleç");
    expect(within(operator).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "eşit",
      "eşit veya üstünde",
      "üstünde",
      "eşit veya altında",
      "altında",
    ]);
    for (const name of ["Metrik", "İşleç", "Eşik", "Sürdürme", "Düzelme", "Pencere", "Önem derecesi"]) {
      expect(screen.getByRole("button", { name: `${name} hakkında` })).toBeInTheDocument();
    }
  });

  it("explains Sustain on focus", async () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    screen.getByRole("button", { name: "Sürdürme hakkında" }).focus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("art arda kaç dilimin ihlal etmesi");
  });

  it("words the unit of the metric choices", () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    expect(screen.getByRole("option", { name: /olay\/sn/ })).toBeInTheDocument();
  });
});
