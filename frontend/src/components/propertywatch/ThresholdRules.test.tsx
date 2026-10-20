import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HOSTILE } from "./fixtures";
import { ThresholdRules } from "./ThresholdRules";

const rules = [
  {
    id: "r-1",
    name: "Backup flood",
    pw_value_id: "id-sb",
    value_key: "svc-backup",
    components: ["svc-backup"],
    enabled: true,
  },
  {
    id: "r-2",
    name: "Admin silence",
    pw_value_id: "id-ad",
    value_key: HOSTILE,
    components: [HOSTILE],
    enabled: false,
  },
  {
    id: "r-3",
    name: "Pair",
    pw_value_id: "id-far",
    value_key: "alice\u001fDENY",
    components: ["alice", "DENY"],
    enabled: true,
  },
];

describe("the Threshold rules tab", () => {
  it("lists each rule with a link, the value it pins and whether it is enabled", () => {
    render(<ThresholdRules rules={rules} access="writer" />);
    const table = screen.getByRole("table", { name: "Threshold rules" });
    expect(within(table).getByRole("link", { name: "Backup flood" })).toHaveAttribute(
      "href",
      "/custom-rules/r-1",
    );
    const row = (name: string) =>
      within(table).getByRole("link", { name }).closest("tr") as HTMLElement;
    expect(row("Backup flood")).toHaveTextContent("svc-backup");
    expect(row("Backup flood")).toHaveTextContent("Enabled");
    expect(row("Admin silence")).toHaveTextContent("Disabled");
    expect(row("Pair")).toHaveTextContent("alice × DENY");
    expect(screen.queryByText(/may no longer be stored/)).toBeNull();
  });

  it("renders hostile value text as text", () => {
    const { container } = render(<ThresholdRules rules={rules} access="writer" />);
    const row = screen
      .getByRole("link", { name: "Admin silence" })
      .closest("tr") as HTMLElement;
    expect(row).toHaveTextContent(HOSTILE);
    expect(container.querySelector("img")).toBeNull();
  });

  it("says no rule pins a value, and offers the create link to a writer", () => {
    render(<ThresholdRules rules={[]} access="writer" />);
    expect(screen.getByText("No rule pins a value of this watch.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("link", { name: "New rule on a value" })).toHaveAttribute(
      "href",
      "/custom-rules/new",
    );
  });

  it("shows a reader the list without the create link, and names the permission", () => {
    render(<ThresholdRules rules={rules} access="reader" />);
    expect(screen.getByRole("table", { name: "Threshold rules" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New rule on a value" })).toBeNull();
    expect(screen.getByText(/Adding a rule needs custom_rule:write/)).toBeInTheDocument();
  });

  it("does the same for a reader on an empty list", () => {
    render(<ThresholdRules rules={[]} access="reader" />);
    expect(screen.getByText("No rule pins a value of this watch.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "New rule on a value" })).toBeNull();
    expect(screen.getByText(/custom_rule:write/)).toBeInTheDocument();
  });

  it("hides the create link and says why when the permissions could not be read", () => {
    render(<ThresholdRules rules={[]} access="unknown" />);
    expect(screen.queryByRole("link", { name: "New rule on a value" })).toBeNull();
    expect(screen.getByText(/permissions could not be read/)).toBeInTheDocument();
  });

  it("says the list could not be loaded when the watch read did not carry it", () => {
    render(<ThresholdRules rules={null} access="writer" />);
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("No rule pins a value of this watch.")).toBeNull();
  });
});
