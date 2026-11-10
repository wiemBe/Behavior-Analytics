import { describe, expect, it } from "vitest";

import { GUIDE_ROUTES, guideFor } from "./guideRoutes";
import { GUIDE_SECTIONS } from "./guideSections";
import { NAV, activeNavHref } from "./nav";

describe("IPS behavior navigation", () => {
  it("has a text entry directly after WAF behavior", () => {
    const labels = NAV[1].items.map((i) => i.label);
    expect(labels.indexOf("IPS behavior")).toBe(labels.indexOf("WAF behavior") + 1);
    expect(NAV[1].items.find((i) => i.label === "IPS behavior")?.href).toBe("/behavior/ips");
  });

  it("activates IPS behavior on its list and on a source, not Overview and not WAF", () => {
    expect(activeNavHref("/behavior/ips")).toBe("/behavior/ips");
    expect(activeNavHref("/behavior/ips/sources/abc-123")).toBe("/behavior/ips");
    expect(activeNavHref("/behavior/waf")).toBe("/behavior/waf");
  });
});

describe("IPS behavior guide routes", () => {
  it("maps both routes to one IPS behavior section that describes them", () => {
    expect(GUIDE_ROUTES["/behavior/ips"]).toBe("ips-behavior");
    expect(GUIDE_ROUTES["/behavior/ips/sources/[id]"]).toBe("ips-behavior");
    expect(guideFor("/behavior/ips")).toBe("ips-behavior");
    const section = GUIDE_SECTIONS.find((s) => s.id === "ips-behavior");
    expect(section?.routes).toEqual(["/behavior/ips", "/behavior/ips/sources/[id]"]);
    expect(section?.permissions).toEqual(["read:anomalies"]);
  });
});
