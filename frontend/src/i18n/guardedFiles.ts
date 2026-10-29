// The files the `react/jsx-no-literals` guard in `.eslintrc.json` covers: every non-test source
// file under these folders. Contract tests walk the same tree, so a file cannot sit outside both.
import { readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";

export const GUARDED_ROOTS = ["src/app", "src/components", "src/widgets", "src/lib"];

const NOT_SOURCE = /\.test\.tsx?$|(^|\/)(fixtures|subjectFixtures|KpiTestStates)\.tsx?$/;

export const FRONTEND_ROOT = resolve(__dirname, "../..");

export function guardedFiles(extension: ".ts" | ".tsx" | "any" = "any"): string[] {
  const found: string[] = [];
  const walk = (relative: string) => {
    for (const entry of readdirSync(join(FRONTEND_ROOT, relative), { withFileTypes: true })) {
      const path = `${relative}/${entry.name}`;
      if (entry.isDirectory()) walk(path);
      else if (/\.tsx?$/.test(entry.name) && !NOT_SOURCE.test(path)) {
        if (extension === "any" || path.endsWith(extension)) found.push(path);
      }
    }
  };
  for (const root of GUARDED_ROOTS) walk(root);
  return found.sort();
}

interface EslintApi {
  calculateConfigForFile(relative: string): Promise<{ rules?: Record<string, unknown> }>;
}

// ESLint ships no type declarations in this project, so it is loaded by `require` and typed here.
const { ESLint } = createRequire(__filename)("eslint") as {
  ESLint: new (options: { cwd: string }) => EslintApi;
};
const eslint = new ESLint({ cwd: FRONTEND_ROOT });

/** The `react/jsx-no-literals` setting ESLint itself resolves for a file (`undefined`: not guarded). */
export async function literalRuleFor(relative: string): Promise<unknown> {
  return (await eslint.calculateConfigForFile(relative)).rules?.["react/jsx-no-literals"];
}
