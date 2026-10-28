// Every message key written as a string literal in `src/` exists in the English catalog:
// `t("title")` after `useTranslations("ns")`, `i18n.t("ns.key")`, `say("ns.key")`, `t.rich(..)`.
// A key built at run time (a template with `${}`) is out of reach and skipped. The namespace of a
// bare `t` comes from the nearest declaration in scope: `const t = useTranslations("ns")` or
// `getTranslations("ns")` is relative to `ns`; a parameter, or a translator over the whole catalog,
// takes full keys.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import { FRONTEND_ROOT, guardedFiles } from "./guardedFiles";

const TRANSLATOR_PROPERTIES = new Set(["t", "say"]);
const TRANSLATOR_METHODS = new Set(["rich", "raw", "markup"]);

function hasKey(path: string): boolean {
  let node: unknown = en;
  for (const part of path.split(".")) {
    if (typeof node !== "object" || node === null || !Object.hasOwn(node, part)) return false;
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string";
}

/** `useTranslations("a.b")` / `await getTranslations("a.b")` -> "a.b"; no argument -> ""; else null. */
function namespaceOf(init: ts.Expression | undefined): string | null {
  let call = init;
  while (call && (ts.isAwaitExpression(call) || ts.isParenthesizedExpression(call))) {
    call = call.expression;
  }
  if (!call || !ts.isCallExpression(call) || !ts.isIdentifier(call.expression)) return null;
  const name = call.expression.text;
  if (name === "asTextTranslator" && call.arguments[0]) return namespaceOf(call.arguments[0]);
  if (name !== "useTranslations" && name !== "getTranslations") return null;
  const arg = call.arguments[0];
  if (arg === undefined) return "";
  return ts.isStringLiteralLike(arg) ? arg.text : null;
}

/**
 * The namespace a bare translator identifier carries at `at`: "" for full keys (a translator over
 * the whole catalog), "a.b" for one made for a namespace, null when it is something else (a local
 * wrapper that adds its own prefix, an untyped parameter) and so cannot be checked.
 */
function scopeNamespace(name: string, at: ts.Node): string | null {
  for (let node: ts.Node | undefined = at.parent; node; node = node.parent) {
    if (ts.isFunctionLike(node)) {
      for (const parameter of node.parameters) {
        if (ts.isIdentifier(parameter.name) && parameter.name.text === name) {
          return parameter.type?.getText().includes("TextTranslator") ? "" : null;
        }
      }
    }
    const statements =
      ts.isBlock(node) || ts.isSourceFile(node) || ts.isModuleBlock(node) ? node.statements : [];
    for (const statement of statements) {
      if (!ts.isVariableStatement(statement)) continue;
      for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name) && declaration.name.text === name) {
          return namespaceOf(declaration.initializer);
        }
      }
    }
  }
  return null;
}

describe("literal message keys", () => {
  it("finds the translator calls it checks", () => {
    const sample = ts.createSourceFile(
      "x.tsx",
      'const t = useTranslations("a"); t("b"); i18n.t("c.d");',
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    expect(sample.statements.length).toBe(3);
    expect(hasKey("shell.connection.connected")).toBe(true);
    expect(hasKey("shell.connection.nope")).toBe(false);
    expect(hasKey("constructor")).toBe(false);
  });

  it("exist in the English catalog", () => {
    const missing: string[] = [];
    let checked = 0;
    for (const relative of guardedFiles("any")) {
      const source = ts.createSourceFile(
        relative,
        readFileSync(resolve(FRONTEND_ROOT, relative), "utf8"),
        ts.ScriptTarget.Latest,
        true,
        relative.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
      );
      const visit = (node: ts.Node) => {
        if (ts.isCallExpression(node) && node.arguments.length > 0) {
          const key = node.arguments[0];
          const callee = node.expression;
          let prefix: string | null = null;
          if (ts.isIdentifier(callee)) {
            if (callee.text === "t" || callee.text === "say") prefix = scopeNamespace(callee.text, node);
          } else if (ts.isPropertyAccessExpression(callee)) {
            const target = callee.expression;
            if (TRANSLATOR_PROPERTIES.has(callee.name.text) && target.kind !== ts.SyntaxKind.ThisKeyword) prefix = "";
            else if (
              TRANSLATOR_METHODS.has(callee.name.text) &&
              ts.isIdentifier(target) &&
              target.text === "t"
            ) {
              prefix = scopeNamespace("t", node);
            }
          }
          if (prefix !== null && ts.isStringLiteralLike(key)) {
            const full = prefix === "" ? key.text : `${prefix}.${key.text}`;
            checked += 1;
            if (!hasKey(full)) {
              missing.push(`${relative}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1} ${full}`);
            }
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
    }
    expect(checked).toBeGreaterThan(1500);
    expect(missing).toEqual([]);
  }, 60_000);
});
