import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

import { FRONTEND_ROOT } from "@/i18n/guardedFiles";
import { GUIDE_EXCLUDED, GUIDE_ROUTES, guideFor } from "./guideRoutes";
import { guideSection } from "./guideSections";

const APP = join(FRONTEND_ROOT, "src/app");
const SRC = join(FRONTEND_ROOT, "src");

function walk(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) walk(path, found);
    else found.push(path);
  }
  return found;
}

/** `src/app/anomalies/[id]/page.tsx` -> `/anomalies/[id]`. */
export function pagePatterns(): string[] {
  return walk(APP)
    .filter((file) => file.endsWith("/page.tsx"))
    .map((file) => relative(APP, file).slice(0, -"page.tsx".length - 1))
    .map((path) => `/${path}`)
    .sort();
}

const SOURCES = walk(SRC)
  .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file))
  .map((file) => ({ file, text: readFileSync(file, "utf8") }));

describe("guide route table", () => {
  const patterns = pagePatterns();

  it("finds the application's pages", () => {
    expect(patterns).toContain("/");
    expect(patterns).toContain("/anomalies/[id]");
    expect(patterns.length).toBeGreaterThan(40);
  });

  it("gives every page an entry or an explicit exclusion, never both", () => {
    const missing = patterns.filter((p) => !(p in GUIDE_ROUTES) && !(p in GUIDE_EXCLUDED));
    expect(missing).toEqual([]);
    expect(patterns.filter((p) => p in GUIDE_ROUTES && p in GUIDE_EXCLUDED)).toEqual([]);
  });

  it("names no route and no exclusion that has no page", () => {
    expect(Object.keys(GUIDE_ROUTES).filter((p) => !patterns.includes(p))).toEqual([]);
    expect(Object.keys(GUIDE_EXCLUDED).filter((p) => !patterns.includes(p))).toEqual([]);
  });

  it("states a reason for each exclusion", () => {
    for (const [route, reason] of Object.entries(GUIDE_EXCLUDED)) {
      expect(reason.length, route).toBeGreaterThan(15);
    }
  });

  it("points every route at a section of the guide", () => {
    for (const [route, id] of Object.entries(GUIDE_ROUTES)) {
      expect(guideSection(id), `${route} -> ${id}`).toBeDefined();
    }
    expect(guideFor("/anomalies/[id]")).toBe("anomaly-detail");
    expect(guideFor("/searches/new")).toBe("searches");
  });

  it("wires every routed page's header: some source asks guideFor for its pattern", () => {
    const unwired = Object.keys(GUIDE_ROUTES).filter(
      (route) => !SOURCES.some(({ text }) => text.includes(`guideFor("${route}")`)),
    );
    expect(unwired).toEqual([]);
  });

  it("spells no section id outside the tables", () => {
    const ids = [...new Set(Object.values(GUIDE_ROUTES))];
    const offenders = SOURCES.filter(
      ({ file }) => !/guideRoutes\.ts$|guideSections\.ts$/.test(file) && !file.includes("/guide/"),
    ).filter(({ text }) => ids.some((id) => text.includes(`guide="${id}"`) || text.includes(`section="${id}"`)));
    expect(offenders.map(({ file }) => file)).toEqual([]);
  });
});
