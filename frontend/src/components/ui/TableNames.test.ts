import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, it } from "vitest";

function productionTsx(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return productionTsx(path);
    return entry.name.endsWith(".tsx") && !entry.name.endsWith(".test.tsx") ? [path] : [];
  });
}

it("gives every production table a caption or accessible label", () => {
  const unnamed: string[] = [];
  for (const path of productionTsx("src")) {
    const source = readFileSync(path, "utf8");
    for (const match of source.matchAll(/<table\b[^>]*>[\s\S]*?<\/table>/g)) {
      const table = match[0];
      const openingTag = table.match(/^<table\b[^>]*>/)?.[0] ?? "";
      if (!/<caption\b/.test(table) && !/aria-label(?:ledby)?=/.test(openingTag)) {
        const line = source.slice(0, match.index).split("\n").length;
        unnamed.push(`${path}:${line}`);
      }
    }
  }
  expect(unnamed, "unnamed tables").toEqual([]);
});
