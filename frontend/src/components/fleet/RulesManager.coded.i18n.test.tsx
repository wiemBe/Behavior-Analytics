// MSG-1b: a coded 409 on a classification rule is worded in the reader's language.

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { RulesManager } from "./RulesManager";
import { ApiError, fleetApi, type ClassificationRule } from "@/lib/api";

function rule(over: Partial<ClassificationRule> = {}): ClassificationRule {
  return {
    id: "r1",
    instance_id: null,
    kind: "NAME_REGEX",
    pattern: "fw-.*",
    target_class: "FIREWALL",
    priority: 1200,
    enabled: true,
    built_in: true,
    description: null,
    created_by: "ada",
    created_at: "",
    updated_at: "",
    ...over,
  };
}

const duplicate = () => {
  const detail = "a rule with this instance, kind and pattern already exists";
  return new ApiError(409, detail, detail, {
    detail,
    detail_code: "classification.rule.duplicate",
    detail_params: {},
  });
};

beforeEach(() => vi.restoreAllMocks());

async function addRule(
  user: ReturnType<typeof userEvent.setup>,
  disclosure: string,
  pattern: string,
  add: string,
) {
  await user.click(screen.getByText(disclosure));
  await user.type(screen.getByRole("textbox", { name: pattern }), "^fw-");
  await user.click(screen.getByRole("button", { name: add }));
}

describe("rules manager: coded conflicts", () => {
  it("words the duplicate refusal in Turkish, at the pattern field", async () => {
    setTestLocale("tr");
    vi.spyOn(fleetApi, "createRule").mockRejectedValue(duplicate());
    const user = userEvent.setup();
    render(<RulesManager initial={[rule()]} />);
    await addRule(user, "Özel kural ekle", "Desen", "Kural ekle");
    expect(
      await screen.findByText("Bu instance, tür ve desene sahip bir kural zaten var"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/already exists/)).toBeNull();
  });

  it("shows the server's English in English", async () => {
    setTestLocale("en");
    vi.spyOn(fleetApi, "createRule").mockRejectedValue(duplicate());
    const user = userEvent.setup();
    render(<RulesManager initial={[rule()]} />);
    await addRule(user, "Add a custom rule", "Pattern", "Add rule");
    expect(
      await screen.findByText("a rule with this instance, kind and pattern already exists"),
    ).toBeInTheDocument();
  });
});
