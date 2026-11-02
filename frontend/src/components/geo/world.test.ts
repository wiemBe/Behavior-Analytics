import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { loadWorld, resetWorld } from "./world";

afterEach(() => {
  resetWorld();
  vi.doUnmock("./world-110m.geo.json");
  vi.resetModules();
});

describe("the outlines", () => {
  it("hold the ISO codes of the countries they draw, and not the ones they leave out", async () => {
    const { codes } = await loadWorld();
    expect(codes.size).toBe(175);
    for (const code of ["US", "DE", "TR", "BR", "ZA", "XK"]) expect(codes.has(code), code).toBe(true);
    // Too small for the 1:110m outlines: the view lists these as not drawn.
    for (const code of ["SG", "MT", "MU", "LI"]) expect(codes.has(code), code).toBe(false);
  });

  it("are loaded once, however many maps ask", async () => {
    const [a, b] = await Promise.all([loadWorld(), loadWorld()]);
    expect(a).toBe(b);
  });

  it("do not remember a failed load, so the next map tries again", async () => {
    vi.resetModules();
    vi.doMock("./world-110m.geo.json", () => {
      throw new Error("chunk failed");
    });
    const world = await import("./world");
    await expect(world.loadWorld()).rejects.toThrow();
    // The same module, the chunk now reachable: a remembered failure would reject again.
    vi.doUnmock("./world-110m.geo.json");
    await expect(world.loadWorld()).resolves.toMatchObject({ codes: expect.any(Set) });
  });
});

describe("the outlines stay out of the main bundle", () => {
  const src = resolve(__dirname, "../..");
  const files = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      return statSync(path).isDirectory() ? files(path) : [path];
    });

  it("are referenced by one dynamic import and by no static import", () => {
    const users = files(src)
      .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f))
      .filter((f) => readFileSync(f, "utf8").includes("world-110m"));
    expect(users.map((f) => relative(src, f))).toEqual(["components/geo/world.ts"]);
    const code = readFileSync(users[0], "utf8");
    expect(code).toMatch(/import\("\.\/world-110m\.geo\.json"\)/);
    expect(code).not.toMatch(/^import .* from "\.\/world-110m/m);
  });

  it("are not on the module path of the dashboard's registry", () => {
    // The registry holds the widget behind a `next/dynamic` loader; nothing in it names the file.
    const registry = readFileSync(resolve(src, "widgets/registry.ts"), "utf8");
    expect(registry).not.toContain("world-110m");
    expect(registry).toContain('import("@/components/catalog/GeographyMap")');
  });
});
