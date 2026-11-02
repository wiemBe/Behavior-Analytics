import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const path = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));

import { GUIDE_EXCLUDED, GUIDE_ROUTES } from "@/lib/guideRoutes";
import { GuideEntry } from "./GuideEntry";

beforeEach(() => {
  path.current = "/";
});

describe("the fixed Guide entry", () => {
  it("links to the guide section of the page it is on", () => {
    path.current = "/anomalies";
    render(<GuideEntry />);
    const link = screen.getByRole("link", { name: "Guide: Anomalies" });
    expect(link).toHaveAttribute("href", "/guide/anomalies");
    expect(link).toHaveTextContent("Guide");
    expect(link).not.toHaveAttribute("aria-current");
  });

  it("follows a detail route to the section that describes it", () => {
    path.current = "/anomalies/abc-123";
    render(<GuideEntry />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/guide/anomaly-detail");
  });

  it("is present on every routed page and links to that page's own section", () => {
    for (const [route, section] of Object.entries(GUIDE_ROUTES)) {
      path.current = route.replace(/\[[^/\]]+\]/g, "abc-123");
      const { unmount } = render(<GuideEntry />);
      expect(screen.getByRole("link").getAttribute("href"), route).toBe(`/guide/${section}`);
      unmount();
    }
  });

  it("links to the index on a page with no section, and on an unknown path", () => {
    for (const route of [...Object.keys(GUIDE_EXCLUDED).filter((r) => r !== "/login"), "/nowhere"]) {
      path.current = route.replace(/\[[^/\]]+\]/g, "x");
      const { unmount } = render(<GuideEntry />);
      const link = screen.getByRole("link", { name: "Guide" });
      expect(link, route).toHaveAttribute("href", "/guide");
      unmount();
    }
  });

  it("marks itself current only on the exact page it links to", () => {
    path.current = "/guide";
    const { unmount } = render(<GuideEntry />);
    expect(screen.getByRole("link")).toHaveAttribute("aria-current", "page");
    unmount();
    path.current = "/guide/roles";
    render(<GuideEntry />);
    expect(screen.getByRole("link")).not.toHaveAttribute("aria-current");
  });

  it("is text only and reachable from the keyboard", async () => {
    path.current = "/rules";
    render(<GuideEntry />);
    const link = screen.getByRole("link");
    expect(link.querySelector("svg, img")).toBeNull();
    await userEvent.tab();
    expect(link).toHaveFocus();
  });
});
