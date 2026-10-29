// The I18N-6 `[id]` route files sit in ESLint's escaped-glob override. The general attribute
// contract reads the first override as plain paths, so these files need the same check here.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const FILES = [
  "src/app/log-sources/[id]/page.tsx",
  "src/app/log-sources/[id]/loading.tsx",
  "src/app/log-sources/[id]/error.tsx",
];

describe("I18N-6 [id] routes translated attribute contract", () => {
  it("leaves no lettered literal accessibility text", () => {
    const root = resolve(__dirname, "../..");
    const failures: string[] = [];
    for (const relative of FILES) {
      const source = ts.createSourceFile(
        relative,
        readFileSync(resolve(root, relative), "utf8"),
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.TSX,
      );
      const visit = (node: ts.Node) => {
        if (
          ts.isJsxAttribute(node) &&
          ["aria-label", "title", "placeholder", "alt"].includes(node.name.getText(source)) &&
          node.initializer != null &&
          ts.isStringLiteral(node.initializer) &&
          /\p{L}/u.test(node.initializer.text)
        ) {
          failures.push(
            `${relative}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`,
          );
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(failures).toEqual([]);
  });
});
