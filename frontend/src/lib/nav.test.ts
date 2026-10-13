import { describe, expect, it } from "vitest";

import { NAV, activeNavHref, navItemMatches } from "./nav";

describe("NAV", () => {
  it("leads with the four behavioral destinations", () => {
    expect(NAV[0].label).toBeNull();
    expect(NAV[0].items.map((i) => i.label)).toEqual([
      "Overview",
      "Anomalies",
      "Sources",
      "WAF behavior",
    ]);
  });

  it("separates supporting capabilities into their own group", () => {
    expect(NAV[1].label).toBe("Platform");
    expect(NAV[1].items.map((i) => i.label)).toEqual([
      "Fleet",
      "Fleet sources",
      "Offenses",
      "Rules",
      "Rule statistics",
      "Coverage",
      "Log Sources",
      "Searches",
      "Alerts",
      "SOC Overview",
    ]);
  });

  it("lists the administration pages under their own group", () => {
    expect(NAV[2].label).toBe("Administration");
    expect(NAV[2].items).toEqual([
      { href: "/admin/search-cadence", label: "Search cadence" },
      { href: "/admin/classification-rules", label: "Classification rules" },
      { href: "/admin/waf-sources", label: "WAF sources" },
    ]);
    expect(activeNavHref("/admin/search-cadence")).toBe("/admin/search-cadence");
    expect(activeNavHref("/admin/classification-rules")).toBe("/admin/classification-rules");
  });

  // A nav entry is a promise that something is on the other side of it.
  it("links to no route that does not exist", () => {
    const hrefs = NAV.flatMap((g) => g.items.map((i) => i.href));
    expect(hrefs).not.toContain("/providers");
    expect(hrefs).not.toContain("/audit");
    expect(hrefs).not.toContain("/config-changes");
    expect(hrefs).not.toContain("/admin");
  });
});

describe("navItemMatches", () => {
  it("matches a section and the detail routes beneath it", () => {
    expect(navItemMatches("/anomalies", "/anomalies")).toBe(true);
    expect(navItemMatches("/anomalies/abc-123", "/anomalies")).toBe(true);
  });

  it("does not match a sibling whose path merely starts the same way", () => {
    expect(navItemMatches("/anomalies-archive", "/anomalies")).toBe(false);
  });

  it("matches the root only exactly, so it cannot claim every route", () => {
    expect(navItemMatches("/", "/")).toBe(true);
    expect(navItemMatches("/offenses", "/")).toBe(false);
  });
});

describe("activeNavHref", () => {
  it("activates Overview on the behavioral overview", () => {
    expect(activeNavHref("/behavior")).toBe("/behavior");
  });

  it("activates Sources on the source index", () => {
    expect(activeNavHref("/behavior/sources")).toBe("/behavior/sources");
  });

  // The nested case that a first-match implementation gets wrong: /behavior is
  // a prefix of /behavior/sources, so ordering alone would light up Overview.
  it("activates Sources on a source detail route, not Overview", () => {
    expect(activeNavHref("/behavior/sources/abc-123")).toBe("/behavior/sources");
  });

  // /behavior is a prefix of /behavior/waf, so ordering alone would light up Overview.
  it("activates WAF behavior on its list and on a WAF source, not Overview", () => {
    expect(activeNavHref("/behavior/waf")).toBe("/behavior/waf");
    expect(activeNavHref("/behavior/waf/sources/abc-123")).toBe("/behavior/waf");
  });

  it("activates Anomalies on the list and on an investigation", () => {
    expect(activeNavHref("/anomalies")).toBe("/anomalies");
    expect(activeNavHref("/anomalies/abc-123")).toBe("/anomalies");
  });

  // /fleet is a prefix of /fleet/sources, so ordering alone would light both.
  it("activates Fleet sources on its own path, and Fleet only on the summary", () => {
    expect(activeNavHref("/fleet")).toBe("/fleet");
    expect(activeNavHref("/fleet/sources")).toBe("/fleet/sources");
  });

  it("activates supporting sections and their detail routes", () => {
    expect(activeNavHref("/offenses")).toBe("/offenses");
    expect(activeNavHref("/rules/42")).toBe("/rules");
    expect(activeNavHref("/rules/statistics")).toBe("/rules/statistics");
    expect(activeNavHref("/log-sources/abc")).toBe("/log-sources");
    expect(activeNavHref("/")).toBe("/");
  });

  it("marks nothing rather than something arbitrary on an unowned path", () => {
    expect(activeNavHref("/nowhere")).toBeNull();
  });
});
