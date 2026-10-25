import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

interface Override {
  files?: string[];
  rules?: Record<string, unknown>;
}

describe("translated attribute contract", () => {
  it("uses the ESLint conversion list and leaves no lettered literal accessibility text", () => {
    const root = resolve(__dirname, "../..");
    const config = JSON.parse(readFileSync(resolve(root, ".eslintrc.json"), "utf8")) as {
      overrides?: Override[];
    };
    const files = config.overrides?.find(
      (entry) => entry.rules?.["react/jsx-no-literals"] != null,
    )?.files;
    expect(files?.length).toBeGreaterThan(0);

    const failures: string[] = [];
    for (const relative of files ?? []) {
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
          failures.push(`${relative}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`);
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(failures).toEqual([]);
  });
});
