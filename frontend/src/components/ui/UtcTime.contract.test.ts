import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, it } from "vitest";

/** Production modules that may call the timestamp formatters. Everywhere else renders `<UtcTime>`. */
const ALLOWED = new Set(["src/lib/health.ts", "src/components/ui/UtcTime.tsx"]);

function productionSources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return productionSources(path);
    if (!entry.name.endsWith(".ts") && !entry.name.endsWith(".tsx")) return [];
    if (entry.name.includes(".test.")) return [];
    return [path];
  });
}

/** Comments are not renders. Strings that contain `://` keep their slashes. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

it("calls formatDateTime only from the timestamp formatters and UtcTime", () => {
  const leaks: string[] = [];
  for (const path of productionSources("src")) {
    if (ALLOWED.has(path)) continue;
    const source = stripComments(readFileSync(path, "utf8"));
    for (const match of source.matchAll(/formatDateTime(?:Compact)?\s*\(/g)) {
      const line = source.slice(0, match.index).split("\n").length;
      leaks.push(`${path}:${line}`);
    }
  }
  expect(leaks, "formatDateTime rendered outside UtcTime").toEqual([]);
});
