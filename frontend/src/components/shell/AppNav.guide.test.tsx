import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/guide/roles" }));

import { NAV, activeNavHref } from "@/lib/nav";
import { setTestLocale } from "../../../vitest.setup";
import { AppNav } from "./AppNav";

describe("Guide navigation entry", () => {
  it("is the last item, in a group of its own after Administration, with no permission", () => {
    const last = NAV[NAV.length - 1];
    expect(last.label).toBeNull();
    expect(last.items).toEqual([{ href: "/guide", label: "Guide" }]);
    const administration = NAV.find((group) => group.label === "Administration")!;
    expect(administration.items.some((item) => item.href === "/guide")).toBe(false);
    expect(NAV.indexOf(administration)).toBe(NAV.length - 2);
  });

  it("is marked current on the guide and on a section", () => {
    expect(activeNavHref("/guide")).toBe("/guide");
    expect(activeNavHref("/guide/roles")).toBe("/guide");
  });

  it("reads Guide in English, as the last link", () => {
    render(<AppNav />);
    const links = within(screen.getByRole("navigation")).getAllByRole("link");
    expect(links[links.length - 1]).toHaveTextContent("Guide");
    expect(links[links.length - 1]).toHaveAttribute("aria-current", "page");
  });

  it("reads Kılavuz in Turkish, as the last link", () => {
    setTestLocale("tr");
    render(<AppNav />);
    const links = within(screen.getByRole("navigation")).getAllByRole("link");
    expect(links[links.length - 1]).toHaveTextContent("Kılavuz");
    expect(links[links.length - 1]).toHaveAttribute("href", "/guide");
  });
});
