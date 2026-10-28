// Each route's provider holds every namespace its browser code reads (I18N-12b). The scan in
// `namespaceScan.ts` walks the import graph from the page and its layouts; the table in
// `routeNamespaces.ts` must equal what it finds (so a missing namespace fails, and so does a stale
// one). A failure prints the line to put in the table.
import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { ReactElement } from "react";
import en from "../../messages/en";
import tr from "../../messages/tr";
import { RouteMessages } from "./RouteMessages";
import { CATALOG_NAMESPACES, chainTo, scanEntries } from "./namespaceScan";
import { FRONTEND_ROOT } from "./guardedFiles";
import {
  BOUNDARY_KEYS,
  ROUTE_NAMESPACES,
  SHELL_NAMESPACES,
  pickMessages,
  routeMessages,
  shellMessages,
} from "./routeNamespaces";

function files(pattern: string): string[] {
  return execSync(`find src/app ${pattern} | sort`, { cwd: FRONTEND_ROOT })
    .toString()
    .trim()
    .split("\n")
    .filter(Boolean);
}

const pages = files("-name page.tsx");
const outsidePage = files("\\( -name error.tsx -o -name loading.tsx -o -name not-found.tsx \\)");

function routeOf(page: string): string {
  return page.replace(/^src\/app/, "").replace(/\/page\.tsx$/, "") || "/";
}

/** The layouts above a page, nearest last. */
function layoutsOf(page: string): string[] {
  const found: string[] = [];
  let directory = page;
  while (directory.includes("/")) {
    directory = directory.slice(0, directory.lastIndexOf("/"));
    if (existsSync(join(FRONTEND_ROOT, directory, "layout.tsx"))) found.push(`${directory}/layout.tsx`);
  }
  return found.reverse();
}

const root = scanEntries(["src/app/layout.tsx", ...outsidePage]);
const shell = [...root.namespaces.keys()].sort();

function line(items: readonly string[]): string {
  return `[${items.map((item) => `"${item}"`).join(", ")}]`;
}

describe("the root provider", () => {
  it("scans cleanly", () => {
    expect(root.unresolved).toEqual([]);
    expect(root.violations).toEqual([]);
  });

  it("carries the namespaces the shell reads", () => {
    const explained = shell.map((n) => `${n}: ${chainTo(root, root.namespaces.get(n)!).join(" > ")}`);
    expect([...SHELL_NAMESPACES], `SHELL_NAMESPACES must be ${line(shell)}\n${explained.join("\n")}`).toEqual(shell);
  });

  it("carries the strings the route boundaries read, and no namespace twice", () => {
    const keys = [...root.keys.keys()].filter((key) => !shell.includes(key.split(".")[0])).sort();
    expect([...BOUNDARY_KEYS], `BOUNDARY_KEYS must be ${line(keys)}`).toEqual(keys);
  });

  it("never sends the guide", () => {
    expect(SHELL_NAMESPACES as readonly string[]).not.toContain("guide");
    for (const catalog of [en, tr]) {
      expect(Object.hasOwn(shellMessages(catalog), "guide")).toBe(false);
    }
  });

  it("holds every boundary string in both catalogs", () => {
    for (const catalog of [en, tr]) {
      const sent = shellMessages(catalog);
      for (const key of BOUNDARY_KEYS) {
        let at: unknown = sent;
        for (const part of key.split(".")) {
          at = typeof at === "object" && at !== null && Object.hasOwn(at, part) ? (at as Record<string, unknown>)[part] : undefined;
        }
        expect(typeof at, key).toBe("string");
      }
    }
  });
});

describe("the route table", () => {
  it("lists every page and nothing else", () => {
    expect(Object.keys(ROUTE_NAMESPACES).sort()).toEqual(pages.map(routeOf).sort());
  });

  it("names only catalog namespaces", () => {
    for (const namespaces of [SHELL_NAMESPACES, ...Object.values(ROUTE_NAMESPACES)]) {
      for (const namespace of namespaces) expect(CATALOG_NAMESPACES, namespace).toContain(namespace);
    }
  });

  it.each(pages)("%s: the page wraps itself in its own route's provider", (page) => {
    const text = readFileSync(join(FRONTEND_ROOT, page), "utf8");
    expect(text).toContain(`export default withRouteMessages("${routeOf(page)}", `);
  });

  it.each(pages)("%s: the set equals what the browser code reads", (page) => {
    const scan = scanEntries([page, ...layoutsOf(page)]);
    expect(scan.unresolved).toEqual([]);
    expect(scan.violations).toEqual([]);
    const needed = [...scan.namespaces.keys()].filter((n) => !shell.includes(n)).sort();
    const route = routeOf(page);
    const explained = needed.map((n) => `${n}: ${chainTo(scan, scan.namespaces.get(n)!).join(" > ")}`);
    expect(
      [...ROUTE_NAMESPACES[route]],
      `"${route}": ${line(needed)},\n${explained.join("\n")}`,
    ).toEqual(needed);
  });
});

describe("what a route is sent", () => {
  it("is the shell plus the route's namespaces, in both languages", () => {
    for (const catalog of [en, tr]) {
      const sent = routeMessages("/login", catalog);
      expect(Object.keys(sent).sort()).toEqual([...SHELL_NAMESPACES, ...ROUTE_NAMESPACES["/login"]].sort());
      expect(sent.login).toBe(catalog.login);
    }
  });

  it("reaches the browser through the page's provider", () => {
    const element = RouteMessages({ route: "/login", children: null }) as ReactElement<{
      messages: Record<string, unknown>;
    }>;
    expect(Object.keys(element.props.messages).sort()).toEqual(
      [...SHELL_NAMESPACES, ...ROUTE_NAMESPACES["/login"]].sort(),
    );
    expect(Object.hasOwn(element.props.messages, "guide")).toBe(false);
  });

  it("refuses a route that is not in the table", () => {
    expect(() => routeMessages("/nowhere", en)).toThrow("No message set for route /nowhere");
  });

  it("picks single keys without the rest of their namespace", () => {
    const sent = pickMessages(en, [], ["rules.boundary.listWhatError", "rules.title", "nope.key"]);
    expect(Object.keys(sent)).toEqual(["rules"]);
    expect(Object.keys(sent.rules as object).sort()).toEqual(["boundary", "title"]);
    expect(Object.keys((sent.rules as { boundary: object }).boundary)).toEqual(["listWhatError"]);
  });

  it("does not follow the prototype chain", () => {
    expect(pickMessages(en, ["constructor", "toString"], ["constructor.name"])).toEqual({});
  });
});
