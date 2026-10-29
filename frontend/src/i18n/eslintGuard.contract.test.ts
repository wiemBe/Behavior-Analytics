// The literal guard is global: every non-test `.ts`/`.tsx` under src/app, src/components,
// src/widgets and src/lib gets `react/jsx-no-literals`, resolved by ESLint itself (not by reading
// the config), so a bracketed route folder (`[id]`) cannot slip out of it.
import { describe, expect, it } from "vitest";
import { guardedFiles, literalRuleFor } from "./guardedFiles";

describe("literal guard coverage", () => {
  it("walks a tree that holds the bracketed route folders", () => {
    const files = guardedFiles("any");
    expect(files.length).toBeGreaterThan(300);
    expect(files).toContain("src/app/anomalies/[id]/page.tsx");
    expect(files).toContain("src/app/admin/ips-sources/[logSourceId]/page.tsx");
    expect(files).toContain("src/app/behavior/waf/sources/[id]/page.tsx");
  });

  it("puts every non-test .tsx under src/app and src/components inside the guard", async () => {
    const outside: string[] = [];
    const tsx = guardedFiles(".tsx").filter(
      (file) => file.startsWith("src/app/") || file.startsWith("src/components/"),
    );
    for (const file of tsx) {
      const rule = (await literalRuleFor(file)) as unknown[] | undefined;
      if (rule == null || rule[0] !== "error") outside.push(file);
    }
    expect(outside).toEqual([]);
  }, 60_000);

  it("covers widgets and lib, and three bracketed paths, with the same options", async () => {
    const paths = [
      "src/app/page.tsx",
      "src/widgets/registry.ts",
      "src/lib/behavior.ts",
      "src/app/anomalies/[id]/page.tsx",
      "src/app/admin/waf-sources/[logSourceId]/loading.tsx",
      "src/app/searches/[id]/error.tsx",
    ];
    for (const path of paths) {
      const rule = (await literalRuleFor(path)) as [string, { noStrings: boolean; allowedStrings: string[] }];
      expect(rule[0], path).toBe("error");
      expect(rule[1].noStrings, path).toBe(true);
      expect(rule[1].allowedStrings, path).toEqual(
        expect.arrayContaining(["—", "·", "…", "/", ":", "(", ")", ",", ".", "≥", "≥ "]),
      );
    }
  });

  it("leaves tests and test builders outside the guard", async () => {
    expect(await literalRuleFor("src/components/home/KpiLogSources.test.tsx")).toBeUndefined();
    expect(await literalRuleFor("src/components/home/KpiTestStates.tsx")).toBeUndefined();
  });
});
