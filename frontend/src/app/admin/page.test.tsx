import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AdminPage from "./page";

// States this route renders, from `page.tsx`: one state only. The hub is a
// static index with no data read, so there is no loading, empty or error state
// and no query parameter. The tests pin what a restyle must not silently change:
// the single h1 and its one-line description, the five destinations in their
// order with their href and their one-line description, and the absence of the
// stale "Phase N" wording the section copy used to carry.

const SECTIONS = [
  {
    name: "Search cadence",
    href: "/admin/search-cadence",
    description: "How often each search family queries QRadar",
  },
  {
    name: "Classification rules",
    href: "/admin/classification-rules",
    description: "Which rules decide a log source's class, and in what order",
  },
  {
    name: "Outage alerts",
    href: "/admin/outage-policy",
    description: "Which silent sources raise an alert, and which are only recorded",
  },
  {
    name: "WAF sources",
    href: "/admin/waf-sources",
    description: "Which QRadar properties each WAF source is read from",
  },
  {
    name: "IPS sources",
    href: "/admin/ips-sources",
    description: "Which QRadar properties each IPS source is read from",
  },
];

function adminSections() {
  return screen.getByRole("list", { name: "Administration sections" });
}

describe("administration hub", () => {
  it("has exactly one h1, named Administration, and states what the page controls", () => {
    render(<AdminPage />);
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("Administration");
    expect(
      screen.getByText(
        "Settings that control collection, source classification and outage alerting.",
      ),
    ).toBeInTheDocument();
  });

  it("lists exactly five sections, in order, each a link", () => {
    render(<AdminPage />);
    const links = within(adminSections()).getAllByRole("link");
    expect(links).toHaveLength(5);
    expect(links.map((a) => a.textContent)).toEqual(SECTIONS.map((s) => s.name));
  });

  it("points each section at its own destination", () => {
    render(<AdminPage />);
    const links = within(adminSections()).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(SECTIONS.map((s) => s.href));
  });

  it("gives each section its one-line description", () => {
    render(<AdminPage />);
    const items = within(adminSections()).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    SECTIONS.forEach((section, i) => {
      expect(within(items[i]).getByText(section.description)).toBeInTheDocument();
    });
  });

  it("carries no stale phase wording", () => {
    render(<AdminPage />);
    expect(document.body.textContent ?? "").not.toMatch(/Phase \d/);
  });
});
