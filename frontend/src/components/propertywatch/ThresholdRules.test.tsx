import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HOSTILE, value } from "./fixtures";
import { ThresholdRules } from "./ThresholdRules";

const rules = [
  { id: "r-1", name: "Backup flood", pw_value_id: "id-sb", enabled: true },
  { id: "r-2", name: "Admin silence", pw_value_id: "id-ad", enabled: false },
  { id: "r-3", name: "Gone", pw_value_id: "id-gone", enabled: true },
];
const values = new Map([
  ["id-sb", value("id-sb", "svc-backup")],
  ["id-ad", value("id-ad", HOSTILE)],
]);

describe("the Threshold rules tab", () => {
  it("lists each rule with a link, the value it pins and whether it is enabled", () => {
    const { container } = render(<ThresholdRules rules={rules} values={values} access="writer" />);
    const table = screen.getByRole("table", { name: "Threshold rules" });
    expect(within(table).getByRole("link", { name: "Backup flood" })).toHaveAttribute("href", "/custom-rules/r-1");
    const row = (name: string) => within(table).getByRole("link", { name }).closest("tr") as HTMLElement;
    expect(row("Backup flood")).toHaveTextContent("svc-backup");
    expect(row("Backup flood")).toHaveTextContent("Enabled");
    expect(row("Admin silence")).toHaveTextContent("Disabled");
    // Attacker-controlled value text is text.
    expect(row("Admin silence")).toHaveTextContent(HOSTILE);
    expect(container.querySelector("img")).toBeNull();
  });

  it("does not invent a value for a rule whose value was not read", () => {
    render(<ThresholdRules rules={rules} values={values} access="writer" />);
    const row = screen.getByRole("link", { name: "Gone" }).closest("tr") as HTMLElement;
    expect(row).toHaveTextContent("not among the tracked values read");
  });

  it("says no rule pins a value, and offers the create link to a writer", () => {
    render(<ThresholdRules rules={[]} values={values} access="writer" />);
    expect(screen.getByText("No rule pins a value of this watch.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("link", { name: "New rule on a value" })).toHaveAttribute("href", "/custom-rules/new");
  });

  it("shows a reader the list without the create link, and names the permission", () => {
    render(<ThresholdRules rules={rules} values={values} access="reader" />);
    expect(screen.getByRole("table", { name: "Threshold rules" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New rule on a value" })).toBeNull();
    expect(screen.getByText(/Adding a rule needs custom_rule:write/)).toBeInTheDocument();
  });

  it("does the same for a reader on an empty list", () => {
    render(<ThresholdRules rules={[]} values={values} access="reader" />);
    expect(screen.getByText("No rule pins a value of this watch.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New rule on a value" })).toBeNull();
    expect(screen.getByText(/custom_rule:write/)).toBeInTheDocument();
  });

  it("hides the create link and says why when the permissions could not be read", () => {
    render(<ThresholdRules rules={[]} values={values} access="unknown" />);
    expect(screen.queryByRole("link", { name: "New rule on a value" })).toBeNull();
    expect(screen.getByText(/permissions could not be read/)).toBeInTheDocument();
  });

  it("says the list could not be loaded when the watch read did not carry it", () => {
    render(<ThresholdRules rules={null} values={null} access="writer" />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("No rule pins a value of this watch.")).toBeNull();
  });
});
