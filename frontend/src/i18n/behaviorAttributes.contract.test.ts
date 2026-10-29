// The two `[id]` pages sit in their own ESLint override (a `[id]` path needs escaping there, and
// `literalAttributes.contract.test.ts` reads the first override's files as plain paths). This is its
// attribute check for them: no lettered literal aria-label, title, placeholder or alt.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const FILES = ["src/app/anomalies/[id]/page.tsx", "src/app/behavior/sources/[id]/page.tsx"];

describe("[id] pages translated attribute contract", () => {
  it("leaves no lettered literal accessibility text", () => {
    const root = resolve(__dirname, "../..");
    const failures: string[] = [];
    for (const relative of FILES) {
      const source = ts.createSourceFile(relative, readFileSync(resolve(root, relative), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const visit = (node: ts.Node) => {
        if (
          ts.isJsxAttribute(node) &&
          ["aria-label", "title", "placeholder", "alt"].includes(node.name.getText(source)) &&
          node.initializer != null &&
          ts.isStringLiteral(node.initializer) &&
          /\p{L}/u.test(node.initializer.text)
        ) {
          failures.push(`${relative}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(failures).toEqual([]);
  });
});
