import { createTranslator } from "next-intl";
import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

import en from "../../messages/en/backend.json";
import tr from "../../messages/tr/backend.json";
import codes from "./backendCodes.generated.json";

interface Catalog {
  [key: string]: string | Catalog;
}

function flatten(catalog: Catalog, prefix = "", out: Record<string, string> = {}) {
  for (const [key, value] of Object.entries(catalog)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else flatten(value, path, out);
  }
  return out;
}

function argumentsOf(message: string): string[] {
  const names = new Set<string>();
  const visit = (elements: MessageFormatElement[]) => {
    for (const element of elements) {
      if (
        element.type === TYPE.argument ||
        element.type === TYPE.number ||
        element.type === TYPE.date ||
        element.type === TYPE.time ||
        element.type === TYPE.select ||
        element.type === TYPE.plural
      ) {
        names.add(element.value);
      }
      if (element.type === TYPE.select || element.type === TYPE.plural) {
        Object.values(element.options).forEach((option) => visit(option.value));
      } else if (element.type === TYPE.tag) {
        visit(element.children);
      }
    }
  };
  visit(parse(message));
  return [...names].sort();
}

describe("generated backend message contract", () => {
  const english = flatten(en);
  const turkish = flatten(tr);

  it("has both locale keys and the exact placeholders for every code", () => {
    for (const [code, spec] of Object.entries(codes)) {
      expect(english[code], `English ${code}`).toBeTypeOf("string");
      expect(turkish[code], `Turkish ${code}`).toBeTypeOf("string");
      expect(argumentsOf(turkish[code]), code).toEqual(Object.keys(spec.params).sort());
    }
  });

  it("has no catalog key without a code in the artifact", () => {
    const known = new Set(Object.keys(codes));
    for (const [name, catalog] of [["English", english], ["Turkish", turkish]] as const) {
      for (const key of Object.keys(catalog)) {
        expect(known.has(key), `${name} key ${key} has no code`).toBe(true);
      }
    }
  });

  it("renders generated English ICU with the registry's sample spelling", () => {
    const t = createTranslator({ locale: "en", messages: { backend: en }, timeZone: "UTC" });
    for (const [code, spec] of Object.entries(codes)) {
      expect(t(`backend.${code}` as never, spec.sample_english_params as never), code).toBe(
        spec.sample_english,
      );
    }
  });
});
