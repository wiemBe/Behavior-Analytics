import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AdminHub from "../page";
import { NAV, activeNavHref } from "@/lib/nav";

describe("administration hub and navigation", () => {
  it("links to the outage alerts page and to the IPS sources page", () => {
    render(<AdminHub />);
    expect(screen.getByRole("link", { name: "Outage alerts" })).toHaveAttribute(
      "href",
      "/admin/outage-policy",
    );
    expect(screen.getByRole("link", { name: "IPS sources" })).toHaveAttribute(
      "href",
      "/admin/ips-sources",
    );
  });

  it("lists Outage alerts once, under Administration, after Classification rules", () => {
    const admin = NAV.find((g) => g.label === "Administration");
    const labels = admin?.items.map((i) => i.label) ?? [];
    expect(labels.filter((l) => l === "Outage alerts")).toHaveLength(1);
    expect(labels.indexOf("Outage alerts")).toBe(labels.indexOf("Classification rules") + 1);
    expect(activeNavHref("/admin/outage-policy")).toBe("/admin/outage-policy");
  });
});
