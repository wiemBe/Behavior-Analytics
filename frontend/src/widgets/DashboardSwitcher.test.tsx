import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DashboardListItem } from "@/lib/dashboardApi";

import { DashboardSwitcher, type DashboardTarget } from "./DashboardSwitcher";

const item = (over: Partial<DashboardListItem>): DashboardListItem => ({
  id: "d1",
  name: "Night shift",
  owner_subject: "me",
  visibility: "PRIVATE",
  template_key: null,
  updated_at: "2026-10-14T10:00:00Z",
  widget_count: 5,
  is_default: false,
  ...over,
});

const items = [
  item({ id: "d1", name: "Night shift", is_default: true }),
  item({ id: "d2", name: "WAF watch", owner_subject: "alice", visibility: "SHARED", widget_count: 1 }),
  item({ id: "d3", name: "Mine, shared", visibility: "SHARED" }),
];

function setup(over: Partial<React.ComponentProps<typeof DashboardSwitcher>> = {}) {
  const onSelect = vi.fn<(target: DashboardTarget) => void>();
  render(
    <DashboardSwitcher
      items={items}
      loading={false}
      failure={null}
      subject="me"
      current={{ kind: "saved", id: "d1" }}
      onSelect={onSelect}
      {...over}
    />,
  );
  return { onSelect, button: screen.getByRole("button", { name: "Dashboards" }) };
}

describe("DashboardSwitcher", () => {
  it("starts closed and lists own, shared, the markers and the template entry when opened", async () => {
    const { button } = setup();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("list", { name: "Saved dashboards" })).toBeNull();
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const saved = within(screen.getByRole("list", { name: "Saved dashboards" })).getAllByRole("button");
    expect(saved.map((b) => b.querySelector(".dg-pop-name")?.textContent)).toEqual([
      "Night shift",
      "WAF watch",
      "Mine, shared",
    ]);
    expect(saved[0]).toHaveTextContent("Default");
    expect(saved[0]).not.toHaveTextContent("Shared");
    expect(saved[1]).toHaveTextContent("Shared");
    expect(saved[1]).toHaveTextContent("owned by alice");
    expect(saved[1]).toHaveTextContent("1 widget");
    expect(saved[2]).toHaveTextContent("Shared");
    expect(saved[2]).not.toHaveTextContent("owned by");
    expect(saved[0]).toHaveAttribute("aria-current", "true");
    expect(saved[1]).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: /SOC Overview \(template\)/ })).toBeInTheDocument();
  });

  it("marks the template entry current when the template is shown", async () => {
    const { button } = setup({ current: { kind: "template" } });
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: /SOC Overview \(template\)/ })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("opens from the keyboard, and Esc closes it and returns focus to the button", async () => {
    const { button } = setup();
    button.focus();
    await userEvent.keyboard("{Enter}");
    expect(button).toHaveAttribute("aria-expanded", "true");
    await userEvent.tab();
    expect(screen.getByRole("button", { name: /Night shift/ })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("closes on a press outside", async () => {
    const { button } = setup();
    await userEvent.click(button);
    await userEvent.click(document.body);
    expect(button).toHaveAttribute("aria-expanded", "false");
  });

  it("selecting a dashboard navigates to it and closes with focus back on the button", async () => {
    const { button, onSelect } = setup();
    await userEvent.click(button);
    await userEvent.click(screen.getByRole("button", { name: /WAF watch/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: "saved", id: "d2" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("selecting the template asks for the template", async () => {
    const { button, onSelect } = setup();
    await userEvent.click(button);
    await userEvent.click(screen.getByRole("button", { name: /SOC Overview \(template\)/ }));
    expect(onSelect).toHaveBeenCalledWith({ kind: "template" });
  });

  it("never invents entries: an empty list says so and still offers the template", async () => {
    const { button } = setup({ items: [] });
    await userEvent.click(button);
    expect(screen.getByText(/You have no saved dashboards, and none is shared with you/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Saved dashboards" })).toBeNull();
    expect(screen.getByRole("button", { name: /SOC Overview \(template\)/ })).toBeInTheDocument();
  });

  it("says the list could not be loaded, with the reason, and offers only the template", async () => {
    const { button } = setup({ items: null, failure: "the server answered HTTP 503" });
    await userEvent.click(button);
    expect(
      screen.getByText("Saved dashboards could not be loaded: the server answered HTTP 503."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Saved dashboards" })).toBeNull();
    expect(screen.getByRole("button", { name: /SOC Overview \(template\)/ })).toBeInTheDocument();
  });

  it("says it is loading while no list is held", async () => {
    const { button } = setup({ items: null, loading: true });
    await userEvent.click(button);
    expect(screen.getByText("Loading dashboards…")).toBeInTheDocument();
  });
});
