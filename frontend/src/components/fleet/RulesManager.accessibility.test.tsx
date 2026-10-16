import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import type { ClassificationRule } from "@/lib/api";

import { RulesManager } from "./RulesManager";

const RULE: ClassificationRule = {
  id: "custom-1",
  instance_id: null,
  kind: "NAME_REGEX",
  pattern: "^edge-",
  target_class: "WAF",
  priority: 100,
  enabled: true,
  built_in: false,
  description: null,
  created_by: "operator",
  created_at: "2026-10-16T06:00:00Z",
  updated_at: "2026-10-16T06:00:00Z",
};

it("focuses the safe delete choice, then Escape returns focus to Delete", async () => {
  const user = userEvent.setup();
  render(<RulesManager initial={[RULE]} />);
  const remove = screen.getByRole("button", { name: /Delete .*edge-/ });

  await user.click(remove);
  expect(screen.getByRole("button", { name: "Keep" })).toHaveFocus();
  await user.keyboard("{Escape}");

  expect(screen.queryByRole("button", { name: "Confirm delete" })).toBeNull();
  expect(remove).toHaveFocus();
});
