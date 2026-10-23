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

/** The opening tag, including a multiline one. Quotes and braces are not tag ends. */
function openingAnchor(source: string, at: number): string {
  let quote: '"' | "'" | "`" | null = null;
  let brace = 0;
  for (let i = at; i < source.length; i++) {
    const c = source[i];
    if (quote) {
      if (c === "\\") {
        i++;
        continue;
      }
      if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      quote = c;
      continue;
    }
    if (c === "{") brace++;
    else if (c === "}") brace = Math.max(0, brace - 1);
    else if (c === ">" && brace === 0) return source.slice(at, i + 1);
  }
  return source.slice(at);
}

/**
 * An internal route on a raw anchor: a literal or template whose path starts
 * with `/`, or one of the builders that only ever produce those paths
 * (`href()`, `sortHref()`, `row.href`, `onViewAllHref`). An in-page `#…` hash
 * is not a route.
 */
function internalRoute(tag: string): boolean {
  const marker = tag.match(/\bhref\s*=\s*/);
  if (!marker || marker.index === undefined) return false;
  const rest = tag.slice(marker.index + marker[0].length);
  if (rest.startsWith('"') || rest.startsWith("'")) {
    const end = rest.indexOf(rest[0], 1);
    return rest.slice(1, end).startsWith("/");
  }
  if (!rest.startsWith("{")) return false;
  const expr = rest.slice(1, rest.lastIndexOf("}")).trim();
  if (/(?:sortHref|href)\s*\(/.test(expr)) return true;
  if (/\.href\b/.test(expr)) return true;
  if (/\bonViewAllHref\b/.test(expr)) return true;
  return /["'`]\/[^"'`]*/.test(expr);
}

it("renders internal routes with next/link, not a raw anchor", () => {
  const raw: string[] = [];
  for (const path of productionTsx("src")) {
    const source = readFileSync(path, "utf8");
    for (const match of source.matchAll(/<a\b/g)) {
      const tag = openingAnchor(source, match.index ?? 0);
      if (!internalRoute(tag)) continue;
      const line = source.slice(0, match.index).split("\n").length;
      raw.push(`${path}:${line}`);
    }
  }
  expect(raw, "internal anchors still using <a>").toEqual([]);
});
