import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RulesManager } from "./RulesManager";
import { ApiError, fleetApi, type ClassificationRule } from "@/lib/api";

function rule(over: Partial<ClassificationRule> = {}): ClassificationRule {
  return {
    id: "r1",
    instance_id: null,
    kind: "NAME_REGEX",
    pattern: "ips-pattern",
    target_class: "IPS",
    priority: 100,
    enabled: true,
    built_in: true,
    description: null,
    created_by: null,
    created_at: "",
    updated_at: "",
    ...over,
  };
}

beforeEach(() => vi.restoreAllMocks());

describe("grouping and built-in rules", () => {
  it("groups rules by level and says when a level is empty", () => {
    render(
      <RulesManager
        initial={[rule(), rule({ id: "r2", kind: "TYPE", pattern: "11", target_class: "WINDOWS" })]}
      />,
    );
    expect(screen.getByRole("heading", { name: "Group rules" })).toBeInTheDocument();
    expect(screen.getByText(/No group rules exist/)).toBeInTheDocument();
    const name = screen.getAllByRole("region", { name: "Name rules" })[0];
    expect(within(name).getByText("ips-pattern")).toBeInTheDocument();
    const type = screen.getAllByRole("region", { name: "Type rules" })[0];
    expect(within(type).getByText("11")).toBeInTheDocument();
  });

  it("offers a built-in rule only its enable toggle", () => {
    render(<RulesManager initial={[rule()]} />);
    expect(screen.getByRole("checkbox", { name: /ips-pattern/ })).toBeChecked();
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("toggles a built-in rule with only enabled in the body", async () => {
    const spy = vi.spyOn(fleetApi, "updateRule").mockResolvedValue(rule({ enabled: false }));
    render(<RulesManager initial={[rule()]} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /ips-pattern/ }));
    expect(spy).toHaveBeenCalledWith("r1", { enabled: false });
    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /ips-pattern/ })).not.toBeChecked();
  });

  it("keeps the toggle where it was on a 403", async () => {
    vi.spyOn(fleetApi, "updateRule").mockRejectedValue(new ApiError(403, "no"));
    render(<RulesManager initial={[rule()]} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /ips-pattern/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("admin:classification");
    expect(screen.getByRole("checkbox", { name: /ips-pattern/ })).toBeChecked();
  });
});

describe("custom rules", () => {
  const custom = rule({ id: "c1", built_in: false, pattern: "mine", created_by: "efe" });

  it("creates a rule and lists it", async () => {
    const created = rule({ id: "c2", built_in: false, pattern: "^new-", target_class: "WAF" });
    const spy = vi.spyOn(fleetApi, "createRule").mockResolvedValue(created);
    render(<RulesManager initial={[]} />);
    await userEvent.click(screen.getByText("Add a custom rule"));
    const form = screen.getByRole("form", { name: "Add a custom rule" });
    await userEvent.type(within(form).getByLabelText("Pattern"), "^new-");
    await userEvent.selectOptions(within(form).getByLabelText("Target class"), "WAF");
    await userEvent.click(within(form).getByRole("button", { name: "Add rule" }));
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "NAME_REGEX", pattern: "^new-", target_class: "WAF", priority: 100 }),
    );
    expect(await screen.findByText("^new-", { selector: "code" })).toBeInTheDocument();
  });

  it("shows a 422 at the pattern field and keeps the input", async () => {
    vi.spyOn(fleetApi, "createRule").mockRejectedValue(
      new ApiError(422, "pattern is not a valid regular expression: missing )"),
    );
    render(<RulesManager initial={[]} />);
    await userEvent.click(screen.getByText("Add a custom rule"));
    const field = screen.getByLabelText("Pattern");
    await userEvent.type(field, "(");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const err = await screen.findByRole("alert");
    expect(err).toHaveTextContent(/not a valid regular expression/);
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field.getAttribute("aria-describedby")).toContain(err.id);
    expect(field).toHaveValue("(");
  });

  it("shows a 409 duplicate at the pattern field", async () => {
    vi.spyOn(fleetApi, "createRule").mockRejectedValue(
      new ApiError(409, "a rule with this instance, kind and pattern already exists"),
    );
    render(<RulesManager initial={[]} />);
    await userEvent.click(screen.getByText("Add a custom rule"));
    await userEvent.type(screen.getByLabelText("Pattern"), "dup");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/);
  });

  it("will not submit an empty pattern", async () => {
    render(<RulesManager initial={[]} />);
    await userEvent.click(screen.getByText("Add a custom rule"));
    expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled();
  });

  it("edits a custom rule", async () => {
    const spy = vi.spyOn(fleetApi, "updateRule").mockResolvedValue({ ...custom, pattern: "mine2" });
    render(<RulesManager initial={[custom]} />);
    await userEvent.click(screen.getByRole("button", { name: /Edit .*mine/ }));
    const form = screen.getByRole("form", { name: /Edit rule/ });
    const input = within(form).getByLabelText("Pattern");
    await userEvent.clear(input);
    await userEvent.type(input, "mine2");
    await userEvent.click(within(form).getByRole("button", { name: "Save rule" }));
    expect(spy).toHaveBeenCalledWith("c1", expect.objectContaining({ pattern: "mine2" }));
    expect(await screen.findByText("mine2", { selector: "code" })).toBeInTheDocument();
  });

  it("deletes after a confirmation", async () => {
    const spy = vi.spyOn(fleetApi, "deleteRule").mockResolvedValue(undefined);
    render(<RulesManager initial={[custom]} />);
    await userEvent.click(screen.getByRole("button", { name: /Delete .*mine/ }));
    expect(spy).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(spy).toHaveBeenCalledWith("c1");
    expect(screen.queryByText("mine", { selector: "code" })).toBeNull();
  });

  it("keeps the rule and says why when delete fails", async () => {
    vi.spyOn(fleetApi, "deleteRule").mockRejectedValue(new ApiError(404, "gone"));
    render(<RulesManager initial={[custom]} />);
    await userEvent.click(screen.getByRole("button", { name: /Delete .*mine/ }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer exists/i);
    expect(screen.getByText("mine", { selector: "code" })).toBeInTheDocument();
  });
});
