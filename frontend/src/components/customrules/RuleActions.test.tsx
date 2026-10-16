import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RuleActions } from "./RuleActions";
import { rule } from "./fixtures";
import { api } from "@/lib/api";

const navigation = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));

describe("custom-rule write actions", () => {
  it("patches enabled against the rule version", async () => {
    vi.spyOn(api, "updateCustomRule").mockResolvedValue(rule({ enabled: false }));
    const value = rule();
    render(<RuleActions rule={value} />);
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    await screen.findByRole("button", { name: "Disable" });
    expect(api.updateCustomRule).toHaveBeenCalledWith(value.id, {
      expected_updated_at: value.updated_at,
      enabled: false,
    });
    expect(navigation.refresh).toHaveBeenCalled();
  });

  it("confirms that delete resolves an open alert before deleting", async () => {
    vi.spyOn(api, "deleteCustomRule").mockResolvedValue(undefined);
    render(<RuleActions rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByRole("alert")).toHaveTextContent("open alert");
    expect(screen.getByRole("alert")).toHaveTextContent("resolved before");
    fireEvent.click(screen.getByRole("button", { name: "Delete rule" }));
    await screen.findByText(/open alert/);
    expect(api.deleteCustomRule).toHaveBeenCalledWith("rule-1");
    expect(navigation.push).toHaveBeenCalledWith("/custom-rules");
  });

  it("names a failed action and tells the operator to reload", async () => {
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(new Error("down"));
    render(<RuleActions rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Reload it and try again");
  });
});
