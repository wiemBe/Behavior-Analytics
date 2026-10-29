// The I18N-7 `[id]` route files: their attributes carry no literal text, and ESLint resolves the
// literal guard for them (I18N-12 turned the escaped file list into globs).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { literalRuleFor } from "./guardedFiles";

const FILES = [
  "src/app/searches/[id]/page.tsx",
  "src/app/searches/[id]/loading.tsx",
  "src/app/searches/[id]/error.tsx",
];

describe("I18N-7 [id] routes translated attribute contract", () => {
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

  it("has ESLint resolve the literal rule for each bracketed route file (the guard is a glob now)", async () => {
    for (const relative of FILES) {
      expect(((await literalRuleFor(relative)) as unknown[] | undefined)?.[0], relative).toBe("error");
    }
  });
});
