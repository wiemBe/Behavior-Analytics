// POL-11. A reader never sees an internal project phase name ("Phase A", "Faz C"): the families
// are named by what they collect. Every value of every en/tr catalog is swept.
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const messagesDir = path.resolve(__dirname, "../../messages");
const PHASE_NAME = /\b(?:Phase|Faz) [A-G]\b/;

function values(node: unknown, trail: string, out: Array<[string, string]>): void {
  if (typeof node === "string") out.push([trail, node]);
  else if (node && typeof node === "object") {
    for (const [key, child] of Object.entries(node)) values(child, `${trail}.${key}`, out);
  }
}

describe("catalog wording", () => {
  for (const locale of ["en", "tr"]) {
    it(`no ${locale} catalog value carries an internal phase name`, () => {
      const dir = path.join(messagesDir, locale);
      const files = readdirSync(dir).filter((name) => name.endsWith(".json"));
      expect(files.length).toBeGreaterThan(0);
      const hits: string[] = [];
      for (const file of files) {
        const found: Array<[string, string]> = [];
        values(JSON.parse(readFileSync(path.join(dir, file), "utf8")), file, found);
        for (const [trail, text] of found) if (PHASE_NAME.test(text)) hits.push(`${trail}: ${text}`);
      }
      expect(hits).toEqual([]);
    });
  }
});
