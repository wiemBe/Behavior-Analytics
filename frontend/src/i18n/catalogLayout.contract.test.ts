import { mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";

const MESSAGES = path.resolve(__dirname, "../../messages");
const LOCALES = ["en", "tr"] as const;

function filesOf(root: string, locale: string): string[] {
  return readdirSync(path.join(root, locale))
    .filter((name) => name.endsWith(".json"))
    .map((name) => name.slice(0, -".json".length))
    .sort();
}

// Reads the assembling module as text: one `import <name> from "./<file>.json"` and one `<name>,`
// entry per namespace, in alphabetical order.
function problemsOf(root: string): string[] {
  const problems: string[] = [];
  const files = Object.fromEntries(LOCALES.map((locale) => [locale, filesOf(root, locale)]));
  for (const locale of LOCALES) {
    const other = LOCALES.find((candidate) => candidate !== locale)!;
    for (const name of files[locale]) {
      if (!files[other].includes(name)) problems.push(`${locale}/${name}.json has no ${other} twin`);
    }
    const source = readFileSync(path.join(root, locale, "index.ts"), "utf8");
    const imports = [...source.matchAll(/^import (\w+) from "\.\/([\w-]+)\.json";$/gm)];
    const body = source.slice(source.indexOf("const messages = {"), source.indexOf("};"));
    const entries = [...body.matchAll(/^ {2}(\w+),$/gm)].map((match) => match[1]);
    const registered = imports.map((match) => match[2]);
    for (const [, identifier, file] of imports) {
      if (identifier !== file) problems.push(`${locale}: ${identifier} is imported from ${file}.json`);
    }
    for (const name of files[locale]) {
      if (!registered.includes(name)) problems.push(`${locale}/${name}.json is not registered`);
    }
    for (const name of registered) {
      if (!files[locale].includes(name)) problems.push(`${locale}: ${name} has no file`);
    }
    const names = imports.map((match) => match[1]);
    if (JSON.stringify(names) !== JSON.stringify([...names].sort())) {
      problems.push(`${locale}: imports are not sorted by namespace name`);
    }
    if (JSON.stringify(entries) !== JSON.stringify(names)) {
      problems.push(`${locale}: object entries differ from the imports or their order`);
    }
  }
  return problems;
}

function fixture(overrides: Record<string, string | null> = {}): string {
  const root = mkdtempSync(path.join(tmpdir(), "catalog-layout-"));
  const indexSource = (names: string[]) =>
    [
      ...names.map((name) => `import ${name} from "./${name}.json";`),
      "",
      "const messages = {",
      ...names.map((name) => `  ${name},`),
      "};",
      "",
      "export default messages;",
      "",
    ].join("\n");
  for (const locale of LOCALES) {
    mkdirSync(path.join(root, locale));
    for (const name of ["alpha", "beta"]) writeFileSync(path.join(root, locale, `${name}.json`), "{}");
    writeFileSync(path.join(root, locale, "index.ts"), indexSource(["alpha", "beta"]));
  }
  for (const [relative, content] of Object.entries(overrides)) {
    if (content === null) writeFileSync(path.join(root, relative), "{}");
    else writeFileSync(path.join(root, relative), content);
  }
  return root;
}

describe("message catalog layout", () => {
  it("has one registered file per namespace in both locales", () => {
    expect(problemsOf(MESSAGES)).toEqual([]);
    expect(Object.keys(en)).toEqual(filesOf(MESSAGES, "en"));
    expect(Object.keys(tr)).toEqual(filesOf(MESSAGES, "tr"));
  });

  it("accepts a consistent layout", () => {
    expect(problemsOf(fixture())).toEqual([]);
  });

  it("fails when a namespace file exists in one locale only", () => {
    const root = fixture({ "en/gamma.json": null });
    expect(problemsOf(root)).toContain("en/gamma.json has no tr twin");
  });

  it("fails when a file is not registered in its module", () => {
    const root = fixture({ "tr/gamma.json": null, "en/gamma.json": null });
    expect(problemsOf(root)).toEqual([
      "en/gamma.json is not registered",
      "tr/gamma.json is not registered",
    ]);
  });

  it("fails when a registered name differs from its file name", () => {
    const root = fixture({
      "tr/index.ts": [
        'import alpha from "./alpha.json";',
        'import beta from "./alpha.json";',
        "",
        "const messages = {",
        "  alpha,",
        "  beta,",
        "};",
        "",
      ].join("\n"),
    });
    const problems = problemsOf(root);
    expect(problems).toContain("tr: beta is imported from alpha.json");
    expect(problems).toContain("tr/beta.json is not registered");
  });

  it("fails when a module registers a name that has no file", () => {
    const root = fixture({
      "en/index.ts": [
        'import alpha from "./alpha.json";',
        'import beta from "./beta.json";',
        'import delta from "./delta.json";',
        "",
        "const messages = {",
        "  alpha,",
        "  beta,",
        "  delta,",
        "};",
        "",
      ].join("\n"),
    });
    expect(problemsOf(root)).toContain("en: delta has no file");
  });

  it("fails when imports or entries are not in alphabetical order", () => {
    const root = fixture({
      "en/index.ts": [
        'import beta from "./beta.json";',
        'import alpha from "./alpha.json";',
        "",
        "const messages = {",
        "  beta,",
        "  alpha,",
        "};",
        "",
      ].join("\n"),
    });
    expect(problemsOf(root)).toContain("en: imports are not sorted by namespace name");
  });

  it("fails when an entry is missing from the object", () => {
    const root = fixture({
      "tr/index.ts": [
        'import alpha from "./alpha.json";',
        'import beta from "./beta.json";',
        "",
        "const messages = {",
        "  alpha,",
        "};",
        "",
      ].join("\n"),
    });
    expect(problemsOf(root)).toContain("tr: object entries differ from the imports or their order");
  });
});
