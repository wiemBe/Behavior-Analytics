// The I18N-7 `[id]` route files sit in ESLint's escaped-glob override. The general attribute
// contract reads the first override as plain paths, so these files need the same check here.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

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

  it("lists each bracketed route file with its brackets escaped, so ESLint reads a path and not a class", () => {
    const root = resolve(__dirname, "../..");
    const config = JSON.parse(readFileSync(resolve(root, ".eslintrc.json"), "utf8")) as {
      overrides: { files: string[]; rules?: Record<string, unknown> }[];
    };
    const listed = config.overrides
      .filter((entry) => entry.rules?.["react/jsx-no-literals"] != null)
      .flatMap((entry) => entry.files);
    for (const relative of FILES) {
      expect(listed, relative).toContain(relative.replaceAll("[", "\\[").replaceAll("]", "\\]"));
      expect(listed, relative).not.toContain(relative);
    }
  });
});
