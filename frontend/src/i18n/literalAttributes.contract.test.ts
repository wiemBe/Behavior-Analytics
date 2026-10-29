import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { FRONTEND_ROOT, guardedFiles } from "./guardedFiles";

describe("translated attribute contract", () => {
  it("walks the guarded tree and leaves no lettered literal accessibility text", () => {
    const files = guardedFiles(".tsx");
    expect(files.length).toBeGreaterThan(200);

    const failures: string[] = [];
    for (const relative of files) {
      const source = ts.createSourceFile(
        relative,
        readFileSync(resolve(FRONTEND_ROOT, relative), "utf8"),
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
          failures.push(`${relative}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(failures).toEqual([]);
  });
});
