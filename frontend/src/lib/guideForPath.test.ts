import { describe, expect, it } from "vitest";

import { GUIDE_EXCLUDED, GUIDE_ROUTES, guideForPath } from "./guideRoutes";

describe("guideForPath", () => {
  it("resolves a concrete path of every routed page to the section its pattern names", () => {
    for (const [route, section] of Object.entries(GUIDE_ROUTES)) {
      const concrete = route.replace(/\[[^/\]]+\]/g, "abc-123");
      expect(guideForPath(concrete), concrete).toBe(section);
    }
  });

  it("prefers a static pattern to a dynamic one", () => {
    expect(guideForPath("/behavior/watches/new")).toBe("property-watch-setup");
    expect(guideForPath("/behavior/watches/7")).toBe("property-watches");
    expect(guideForPath("/searches/new")).toBe("searches");
    expect(guideForPath("/rules/statistics")).toBe("rule-statistics");
    expect(guideForPath("/rules/42")).toBe("rules");
  });

  it("ignores a trailing slash", () => {
    expect(guideForPath("/anomalies/")).toBe("anomalies");
    expect(guideForPath("/")).toBe("dashboard");
  });

  it("answers undefined for a page with no section and for an unknown path", () => {
    for (const route of Object.keys(GUIDE_EXCLUDED)) {
      expect(guideForPath(route.replace(/\[[^/\]]+\]/g, "x")), route).toBeUndefined();
    }
    expect(guideForPath("/nowhere")).toBeUndefined();
    expect(guideForPath("/anomalies/1/extra")).toBeUndefined();
    expect(guideForPath("/guide")).toBeUndefined();
    expect(guideForPath("/guide/roles")).toBeUndefined();
  });
});
