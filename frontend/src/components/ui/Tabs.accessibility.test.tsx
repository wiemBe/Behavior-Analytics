import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { Tabs } from "./Tabs";

const tabs = [
  { id: "evidence", label: "Evidence", content: () => <p>Evidence panel</p> },
  { id: "lifecycle", label: "Lifecycle", content: () => <p>Lifecycle panel</p> },
  { id: "technical", label: "Technical", content: () => <p>Technical panel</p> },
];

it("only exposes aria-controls for the tab whose lazily mounted panel exists", async () => {
  const user = userEvent.setup();
  const { container } = render(<Tabs tabs={tabs} label="Investigation" />);

  const evidence = screen.getByRole("tab", { name: "Evidence" });
  const lifecycle = screen.getByRole("tab", { name: "Lifecycle" });
  expect(container.querySelector(`#${CSS.escape(evidence.getAttribute("aria-controls")!)}`)).toBe(
    screen.getByRole("tabpanel"),
  );
  expect(lifecycle).not.toHaveAttribute("aria-controls");

  await user.click(lifecycle);
  expect(evidence).not.toHaveAttribute("aria-controls");
  expect(
    container.querySelector(`#${CSS.escape(lifecycle.getAttribute("aria-controls")!)}`),
  ).toBe(screen.getByRole("tabpanel"));
});
