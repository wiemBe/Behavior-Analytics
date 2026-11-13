// The navigation's collapsible groups (UIQ-5): which groups start open, that the group holding
// the current page cannot be closed, the disclosure's ARIA contract, and the remembered choice.

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppNav } from "./AppNav";
import { NAV } from "@/lib/nav";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

const KEY = "qradar-dash.nav.groups";

function renderAt(path: string) {
  pathname.current = path;
  return render(<AppNav />);
}
const toggle = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });

describe("AppNav groups", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("keeps every entry in the document and marks the closed groups' lists inert", () => {
    const { container } = renderAt("/alerts");
    const total = NAV.reduce((n, g) => n + g.items.length, 0);
    expect(container.querySelectorAll("a.nav-link")).toHaveLength(total);
    expect(container.querySelectorAll("ul.nav-list[inert]")).toHaveLength(4);
  });

  it("opens Behavior on a page no labeled group owns, and only the current group elsewhere", () => {
    renderAt("/");
    expect(toggle("Behavior")).toHaveAttribute("aria-expanded", "true");
    expect(toggle("Detection")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("link", { name: "Queue" })).toBeInTheDocument();
  });

  it("opens the group of the current page and leaves the others closed", () => {
    renderAt("/admin/users");
    expect(toggle("Administration")).toHaveAttribute("aria-expanded", "true");
    expect(toggle("Behavior")).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(toggle("Behavior").getAttribute("aria-controls")!)).toHaveAttribute(
      "inert",
    );
  });

  it("cannot close the group that holds the current page", () => {
    renderAt("/alerts");
    const button = toggle("Detection");
    expect(button).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("opens a closed group with the button, names the list it controls, and remembers it", () => {
    renderAt("/alerts");
    const button = toggle("Fleet");
    const list = document.getElementById(button.getAttribute("aria-controls")!);
    expect(list).toHaveAttribute("inert");
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(list).not.toHaveAttribute("inert");
    expect(screen.getByRole("list", { name: "Fleet" })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual({ Fleet: true });
  });

  it("applies a remembered choice after mount, including a closed Behavior on the Dashboard", () => {
    window.localStorage.setItem(KEY, JSON.stringify({ Behavior: false, Fleet: true }));
    renderAt("/");
    expect(toggle("Behavior")).toHaveAttribute("aria-expanded", "false");
    expect(toggle("Fleet")).toHaveAttribute("aria-expanded", "true");
  });

  it("ignores unreadable stored state and still works when storage throws", () => {
    window.localStorage.setItem(KEY, "{not json");
    renderAt("/");
    expect(toggle("Behavior")).toHaveAttribute("aria-expanded", "true");
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    fireEvent.click(toggle("Fleet"));
    expect(toggle("Fleet")).toHaveAttribute("aria-expanded", "true");
    spy.mockRestore();
  });

  it("is operable from the keyboard: the toggles are native buttons in tab order", () => {
    renderAt("/alerts");
    for (const name of ["Behavior", "Fleet", "Detection", "Operations", "Administration"]) {
      const b = toggle(name);
      expect(b.tagName).toBe("BUTTON");
      expect(b).not.toBeDisabled();
    }
  });
});
