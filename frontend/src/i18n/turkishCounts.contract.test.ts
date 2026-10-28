import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

import tr from "../../messages/tr";
import { CONVERTED, COUNT_NAMES, PLAIN } from "./turkishCounts.inventory";

// I18N-12c: a Turkish value with a plain placeholder prints the number the caller passes as it is
// (`1204 kaynak` beside `1.204` everywhere else). A placeholder that a caller fills with a raw
// number that can reach 1,000 is written `{name, number}`. The inventory lives in
// `turkishCounts.inventory.ts`; this test pins it in both directions.

function flatten(node: unknown, path = ""): Array<[string, string]> {
  if (typeof node === "string") return [[path, node]];
  if (node && typeof node === "object") {
    return Object.entries(node).flatMap(([key, value]) => flatten(value, path ? `${path}.${key}` : key));
  }
  return [];
}

const turkish = new Map(flatten(tr));
const translate = createTranslator({ locale: "tr", messages: tr as never, timeZone: "UTC" });

/** Renders one value, a rich tag (`<link>`, `<num>`) as its own text. */
function render(key: string, value: string, values: Record<string, string | number>): string {
  const tags = [...value.matchAll(/<(\w+)>/g)].map((match) => match[1]);
  const handlers = Object.fromEntries(
    tags.map((tag) => [tag, (chunks: unknown) => (Array.isArray(chunks) ? chunks.join("") : String(chunks))]),
  );
  const out = (translate as never as { rich: (k: string, v: object) => unknown }).rich(key, {
    ...values,
    ...handlers,
  });
  return Array.isArray(out) ? out.join("") : String(out);
}

/** Every argument name a value uses, whatever its format (`{x}`, `{x, number}`, `{x, plural, ...}`). */
function argumentsOf(value: string): string[] {
  return [...new Set([...value.matchAll(/\{(\w+)\s*[,}]/g)].map((match) => match[1]))];
}

function plainPlaceholders(value: string): string[] {
  return [...new Set([...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]))];
}

describe("Turkish counts above 999", () => {
  it("renders every converted placeholder with the Turkish grouping", () => {
    for (const [key, names] of Object.entries(CONVERTED)) {
      const value = turkish.get(key);
      expect(value, `${key} is missing from the Turkish catalog`).toBeDefined();
      const values: Record<string, string | number> = {};
      for (const name of argumentsOf(value as string)) values[name] = "x";
      for (const name of names) values[name] = 1204;
      const text = render(key, value as string, values);
      expect(text, key).not.toContain("1204");
      expect(text.split("1.204").length - 1, `${key}: ${text}`).toBe(names.length);
    }
  });

  it("keeps every converted placeholder out of the plain form", () => {
    const plain = Object.entries(CONVERTED).flatMap(([key, names]) =>
      names
        .filter((name) => plainPlaceholders(turkish.get(key) ?? "").includes(name))
        .map((name) => `${key}: {${name}}`),
    );
    expect(plain).toEqual([]);
  });

  it("classifies every plain count-like placeholder of the Turkish catalog", () => {
    const unclassified: string[] = [];
    for (const [key, value] of turkish) {
      for (const name of plainPlaceholders(value)) {
        if (!COUNT_NAMES.includes(name)) continue;
        if (!Object.hasOwn(PLAIN, key) || !Object.hasOwn(PLAIN[key], name)) {
          unclassified.push(`${key}: {${name}}`);
        }
      }
    }
    // A raw number that can reach 1,000 is written `{name, number}`; a string the caller already
    // formatted, or a bounded number, is added to PLAIN with its reason.
    expect(unclassified).toEqual([]);
  });

  it("drops a plain entry once its placeholder is converted or removed", () => {
    const stale = Object.entries(PLAIN).flatMap(([key, names]) =>
      Object.keys(names)
        .filter((name) => !plainPlaceholders(turkish.get(key) ?? "").includes(name))
        .map((name) => `${key}: {${name}}`),
    );
    expect(stale).toEqual([]);
  });
});
