// Which catalog namespaces the browser code of a route can read (I18N-12b). Test support: the
// contract test in `routeNamespaces.contract.test.ts` walks the import graph from a route's
// `page.tsx` / `layout.tsx` and compares the result with `routeNamespaces.ts`.
//
// The walk starts on the server. A module that opens with "use client" switches the walk to the
// browser and everything it imports stays in the browser; the page itself and the server-only
// modules it imports are not collected, because they read the whole catalog through
// `getTranslations`. In the browser a module counts for
//   - the first segment of a literal `useTranslations("ns.sub")` argument,
//   - the first segment of any string or template literal that starts with `ns.` for a catalog
//     namespace (`say("home.eps.title")`, a key table `{ label: "enums.severity.high" }`),
// An area English bundle (`messages/en/<ns>.json`) is not a provider read: the helper that imports
// it formats with it directly. It is listed in `bundles` so that its weight can be reviewed.
//   - `export const I18N_NAMESPACES = [...]`, which a module declares when a key is assembled so
//     that no literal shows its namespace.
// A `useTranslations(<expression>)` with no literal argument is reported as unresolved.
//
// A route's `error.tsx`, `loading.tsx` and `not-found.tsx` render outside the page, so they read the root
// provider, not the page's. Those two are not counted by namespace: the scan reads the key behind
// each `t("key")` call of a translator made by `useTranslations("ns")` and reports it in `keys`
// (`rules.boundary.listWhatError`), so that the root provider can carry just those strings.//
// One exception to "everything a browser module imports counts": a module may list helpers it calls
// without a translator in `export const I18N_ENGLISH_DEFAULT_CALLS = ["helper", ...]`. Those
// helpers format with their own English default, so they read nothing from the provider. The walk
// does not enter the module they come from (when all its named imports are listed) and checks
// the claim instead: every call of a listed helper must pass fewer arguments than the position of
// the helper's translator parameter (the one defaulted to `ENGLISH_*`).
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import ts from "typescript";

import en from "../../messages/en";
import { FRONTEND_ROOT } from "./guardedFiles";

export const CATALOG_NAMESPACES: readonly string[] = Object.keys(en);

const SRC = join(FRONTEND_ROOT, "src");
const KEY_START = /^([A-Za-z][A-Za-z0-9]*)\.[A-Za-z]/;
// A template that opens with a namespace and a dot (`fieldMapping.${key}`) builds its key after that.
const TEMPLATE_START = /^([A-Za-z][A-Za-z0-9]*)\.$/;

export interface Scan {
  /** Namespaces the browser code reads, with the first file that reads each. */
  namespaces: Map<string, string>;
  /** `useTranslations(<expression>)` calls whose namespace is not a literal. */
  unresolved: string[];
  /** Area English bundles (`messages/en/<ns>.json`) the browser code imports, per importing file. */
  bundles: Map<string, string>;
  /** Full key paths read by the route boundary files (`error.tsx`), with the file that reads each. */
  keys: Map<string, string>;
  /** Listed English-default calls that pass a translator, or whose helper cannot be checked. */
  violations: string[];
  /** Every module the walk visited in the browser. */
  browserFiles: Set<string>;
  /** For each browser module, the module that first imported it (for explaining a namespace). */
  importedBy: Map<string, string>;
}

function resolveModule(from: string, specifier: string): string | null {
  let base: string;
  if (specifier.startsWith("@/")) base = join(SRC, specifier.slice(2));
  else if (specifier.startsWith(".")) base = resolve(dirname(from), specifier);
  else return null;
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function isUseClient(source: ts.SourceFile): boolean {
  for (const statement of source.statements) {
    if (
      ts.isExpressionStatement(statement) &&
      ts.isStringLiteral(statement.expression) &&
      statement.expression.text !== "use strict"
    ) {
      if (statement.expression.text === "use client") return true;
      continue;
    }
    break;
  }
  return false;
}

function bundleNamespace(path: string): string | null {
  const match = /messages\/en\/([A-Za-z0-9]+)\.json$/.exec(path);
  return match ? match[1] : null;
}

function collect(source: ts.SourceFile, file: string, into: Scan) {
  const add = (namespace: string) => {
    if (CATALOG_NAMESPACES.includes(namespace) && !into.namespaces.has(namespace)) {
      into.namespaces.set(namespace, file);
    }
  };
  const visit = (node: ts.Node) => {
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === "useTranslations" &&
      node.arguments.length > 0
    ) {
      const argument = node.arguments[0];
      if (ts.isStringLiteralLike(argument)) add(argument.text.split(".")[0]);
      else into.unresolved.push(`${file}: ${node.getText(source)}`);
    }
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === "I18N_NAMESPACES" &&
      node.initializer
    ) {
      let list: ts.Expression = node.initializer;
      while (ts.isAsExpression(list)) list = list.expression;
      if (ts.isArrayLiteralExpression(list)) {
        for (const element of list.elements) if (ts.isStringLiteralLike(element)) add(element.text);
      }
    }
    if (
      ts.isStringLiteralLike(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      const match =
        KEY_START.exec(node.text) ?? (ts.isTemplateHead(node) ? TEMPLATE_START.exec(node.text) : null);
      if (match) add(match[1]);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}

/** The helper names a module declares in `I18N_ENGLISH_DEFAULT_CALLS`. */
function englishDefaultCalls(source: ts.SourceFile): string[] {
  const names: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === "I18N_ENGLISH_DEFAULT_CALLS" &&
      node.initializer
    ) {
      let list: ts.Expression = node.initializer;
      while (ts.isAsExpression(list)) list = list.expression;
      if (ts.isArrayLiteralExpression(list)) {
        for (const element of list.elements) if (ts.isStringLiteralLike(element)) names.push(element.text);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return names;
}

/** The index of a function's translator parameter (defaulted to `ENGLISH_*`), -1 if it has none. */
function translatorPosition(target: string, name: string): number | null {
  const source = ts.createSourceFile(target, readFileSync(target, "utf8"), ts.ScriptTarget.Latest, true);
  for (const statement of source.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name?.text === name) {
      const parameters = statement.parameters;
      const at = parameters.findIndex(
        (parameter) =>
          parameter.initializer !== undefined &&
          /^ENGLISH_/.test(parameter.initializer.getText(source)),
      );
      if (at >= 0) return at;
      const typed = parameters.some((parameter) =>
        /I18n|Translator/.test(parameter.type?.getText(source) ?? ""),
      );
      return typed ? null : -1;
    }
  }
  return null;
}

function checkEnglishDefaultCalls(
  source: ts.SourceFile,
  file: string,
  listed: string[],
  into: Scan,
) {
  const origin = new Map<string, string>();
  for (const statement of source.statements) {
    if (
      ts.isImportDeclaration(statement) &&
      ts.isStringLiteral(statement.moduleSpecifier) &&
      statement.importClause?.namedBindings &&
      ts.isNamedImports(statement.importClause.namedBindings)
    ) {
      const target = resolveModule(file, statement.moduleSpecifier.text);
      if (target === null) continue;
      for (const element of statement.importClause.namedBindings.elements) {
        origin.set(element.name.text, `${target}|${(element.propertyName ?? element.name).text}`);
      }
    }
  }
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression)) {
      const name = node.expression.text;
      if (listed.includes(name)) {
        const [target, original] = (origin.get(name) ?? "|").split("|");
        const at = target ? translatorPosition(target, original) : null;
        if (at === null) into.violations.push(`${file}: cannot check ${name}`);
        else if (at >= 0 && node.arguments.length > at) {
          into.violations.push(`${file}: ${name} is called with a translator`);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
}

const BOUNDARY_FILE = /\/src\/app\/(?:.*\/)?(?:error|loading|not-found)\.tsx$/;

function collectKeys(source: ts.SourceFile, file: string, into: Scan) {
  const prefixes = new Map<string, string>();
  const strip = (node: ts.Expression | undefined): ts.Expression | undefined => {
    let at = node;
    while (at && (ts.isAwaitExpression(at) || ts.isParenthesizedExpression(at) || ts.isAsExpression(at))) {
      at = at.expression;
    }
    return at;
  };
  const declare = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) {
      const call = strip(node.initializer);
      if (
        call &&
        ts.isCallExpression(call) &&
        ts.isIdentifier(call.expression) &&
        call.expression.text === "useTranslations"
      ) {
        const argument = call.arguments[0];
        if (argument === undefined) prefixes.set(node.name.text, "");
        else if (ts.isStringLiteralLike(argument)) prefixes.set(node.name.text, argument.text);
        else into.unresolved.push(`${file}: ${call.getText(source)}`);
      }
    }
    ts.forEachChild(node, declare);
  };
  declare(source);
  const readCalls = (node: ts.Node) => {
    if (ts.isCallExpression(node)) {
      const callee = ts.isPropertyAccessExpression(node.expression)
        ? node.expression.expression
        : node.expression;
      if (ts.isIdentifier(callee) && prefixes.has(callee.text)) {
        const argument = node.arguments[0];
        if (argument && ts.isStringLiteralLike(argument)) {
          const prefix = prefixes.get(callee.text)!;
          const key = prefix ? `${prefix}.${argument.text}` : argument.text;
          if (!into.keys.has(key)) into.keys.set(key, file);
        } else into.unresolved.push(`${file}: ${node.getText(source)}`);
        for (const argument of node.arguments) ts.forEachChild(argument, readCalls);
        return;
      }
    }
    if (ts.isIdentifier(node) && prefixes.has(node.text)) {
      const parent = node.parent;
      const declared = ts.isVariableDeclaration(parent) && parent.name === node;
      if (!declared) into.unresolved.push(`${file}: translator "${node.text}" is passed on`);
    }
    ts.forEachChild(node, readCalls);
  };
  readCalls(source);
}

function importsOf(source: ts.SourceFile, englishDefault: string[]): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isImportDeclaration(node) &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      node.importClause?.namedBindings &&
      ts.isNamedImports(node.importClause.namedBindings) &&
      !node.importClause.name &&
      node.importClause.namedBindings.elements.every((element) =>
        englishDefault.includes(element.name.text),
      )
    ) {
      // Every name comes from the English-default list: the module is not entered.
    } else if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      found.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments[0] &&
      ts.isStringLiteralLike(node.arguments[0])
    ) {
      found.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

/** The namespaces the browser code reachable from `entries` (paths from the frontend root) reads. */
export function scanEntries(entries: string[]): Scan {
  const scan: Scan = { namespaces: new Map(), unresolved: [], keys: new Map(), violations: [], bundles: new Map(), browserFiles: new Set(), importedBy: new Map() };
  const seen = new Set<string>();
  const stack: Array<[string, boolean, string]> = entries.map((entry) => [join(FRONTEND_ROOT, entry), false, ""]);
  while (stack.length > 0) {
    const [file, inBrowser, parent] = stack.pop()!;
    const key = `${file}|${inBrowser}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const text = readFileSync(file, "utf8");
    const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const browser = inBrowser || isUseClient(source);
    if (browser) {
      scan.browserFiles.add(file);
      if (!scan.importedBy.has(file)) scan.importedBy.set(file, parent);
      if (BOUNDARY_FILE.test(file)) collectKeys(source, file, scan);
      else collect(source, file, scan);
    }
    const listed = browser ? englishDefaultCalls(source) : [];
    if (listed.length > 0) checkEnglishDefaultCalls(source, file, listed, scan);
    for (const specifier of importsOf(source, listed)) {
      const bundle = bundleNamespace(specifier);
      if (bundle !== null) {
        if (browser) scan.bundles.set(`${file}|${bundle}`, bundle);
        continue;
      }
      const next = resolveModule(file, specifier);
      if (next !== null && /\.tsx?$/.test(next)) stack.push([next, browser, file]);
    }
  }
  return scan;
}

/** The import chain from an entry to `file`, for a failure message. */
export function chainTo(scan: Scan, file: string): string[] {
  const chain: string[] = [];
  for (let at: string | undefined = file; at; at = scan.importedBy.get(at)) {
    chain.unshift(at.replace(`${FRONTEND_ROOT}/`, ""));
    if (chain.length > 30) break;
  }
  return chain;
}
