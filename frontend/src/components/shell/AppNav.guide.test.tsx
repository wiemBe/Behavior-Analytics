import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/guide/roles" }));

import { NAV, activeNavHref } from "@/lib/nav";
import { AppNav } from "./AppNav";

// GUIDE-3: the Guide is no longer an entry of the scrolling list. The fixed entry at the bottom
// of the column (GuideEntry) replaces it, so a second link here would be a duplicate.
describe("Guide is not in the navigation list", () => {
  it("has no /guide entry in any group", () => {
    expect(NAV.flatMap((group) => group.items.map((item) => item.href))).not.toContain("/guide");
    expect(NAV[NAV.length - 1].label).toBe("Administration");
  });

  it("marks no list entry current on the guide", () => {
    expect(activeNavHref("/guide")).toBeNull();
    expect(activeNavHref("/guide/roles")).toBeNull();
  });

  it("renders no Guide link", () => {
    render(<AppNav />);
    const hrefs = within(screen.getByRole("navigation")).getAllByRole("link").map((l) => l.getAttribute("href"));
    expect(hrefs.some((href) => href?.startsWith("/guide"))).toBe(false);
    expect(screen.queryByRole("link", { name: "Guide" })).toBeNull();
  });

  it("lists the admin hub first under Administration", () => {
    render(<AppNav />);
    const list = within(screen.getByRole("navigation")).getByRole("list", { name: "Administration" });
    const names = within(list).getAllByRole("link").map((l) => l.textContent);
    expect(names[0]).toBe("Administration overview");
    expect(names).not.toContain("Config changes");
    expect(within(list).getByRole("link", { name: "Administration overview" })).toHaveAttribute("href", "/admin");
  });
});
