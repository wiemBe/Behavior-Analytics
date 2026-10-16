import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AlertActions } from "./AlertActions";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

it("closes the resolve confirmation with Escape and returns focus to Resolve", async () => {
  const user = userEvent.setup();
  render(<AlertActions alertId="a-1" status="OPEN" />);
  const resolve = screen.getByRole("button", { name: "Resolve" });

  await user.click(resolve);
  expect(screen.getByRole("button", { name: "Resolve alert" })).toHaveFocus();
  await user.keyboard("{Escape}");

  expect(screen.queryByRole("group", { name: "Confirm resolve" })).toBeNull();
  expect(resolve).toHaveFocus();
});
