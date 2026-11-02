import { createTranslator } from "next-intl";
import { parse, TYPE, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { describe, expect, it } from "vitest";

import en from "../../messages/en/backend.json";
import tr from "../../messages/tr/backend.json";
import codes from "./backendCodes.generated.json";
import { ENABLED_LOCALES, type Locale } from "./locale";
import { FROZEN_TR_KEYS } from "./trFrozen";

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

interface CodeSpec {
  params: Record<string, unknown>;
}

/**
 * Turkish is frozen at the tag `checkpoint/bilingual-2026-11-02` (D-41): a Turkish entry is
 * required only for a code that had one then (`tr-frozen-keys.json`, the `backend.` keys), and
 * again for every code once `tr` is enabled. A Turkish entry that exists is always held to the
 * code's exact placeholders.
 */
function problemsOf(
  registry: Record<string, CodeSpec>,
  english: Record<string, string>,
  turkish: Record<string, string>,
  frozenCodes: ReadonlySet<string>,
  enabled: readonly Locale[] = ENABLED_LOCALES,
): string[] {
  const problems: string[] = [];
  for (const [code, spec] of Object.entries(registry)) {
    if (typeof english[code] !== "string") problems.push(`English ${code} is missing`);
    const text = turkish[code];
    if (typeof text !== "string") {
      if (frozenCodes.has(code) || enabled.includes("tr")) {
        problems.push(`Turkish ${code} is missing`);
      }
      continue;
    }
    const expected = Object.keys(spec.params).sort().join(",");
    if (argumentsOf(text).join(",") !== expected) {
      problems.push(`Turkish ${code} placeholders differ from [${expected}]`);
    }
  }
  for (const [name, catalog] of [["English", english], ["Turkish", turkish]] as const) {
    for (const key of Object.keys(catalog)) {
      if (!(key in registry)) problems.push(`${name} key ${key} has no code`);
    }
  }
  return problems.sort();
}

const FROZEN_CODES: ReadonlySet<string> = new Set(
  FROZEN_TR_KEYS.filter((key) => key.startsWith("backend.")).map((key) =>
    key.slice("backend.".length),
  ),
);

describe("generated backend message contract", () => {
  const english = flatten(en);
  const turkish = flatten(tr);

  it("has English for every code, Turkish for every frozen code, and exact placeholders", () => {
    expect(FROZEN_CODES.size).toBeGreaterThan(700);
    expect(problemsOf(codes, english, turkish, FROZEN_CODES)).toEqual([]);
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

describe("the frozen-Turkish rule for backend codes (D-41)", () => {
  const registry = {
    "old.code": { params: { n: "int" } },
    "new.code": { params: { name: "str" } },
  };
  const english = { "old.code": "{n} old", "new.code": "New {name}" };
  const frozen = new Set(["old.code"]);

  it("accepts a code added after the checkpoint that has no Turkish entry", () => {
    expect(problemsOf(registry, english, { "old.code": "{n} eski" }, frozen, ["en"])).toEqual([]);
  });

  it("accepts a Turkish entry for a new code when its placeholders are exact", () => {
    const turkish = { "old.code": "{n} eski", "new.code": "Yeni {name}" };
    expect(problemsOf(registry, english, turkish, frozen, ["en"])).toEqual([]);
  });

  it("rejects a deleted frozen Turkish entry", () => {
    expect(problemsOf(registry, english, {}, frozen, ["en"])).toEqual([
      "Turkish old.code is missing",
    ]);
  });

  it("rejects a Turkish entry for a code that does not exist", () => {
    const turkish = { "old.code": "{n} eski", "gone.code": "gitti" };
    expect(problemsOf(registry, english, turkish, frozen, ["en"])).toEqual([
      "Turkish key gone.code has no code",
    ]);
  });

  it("holds an existing Turkish entry to the exact placeholders", () => {
    const turkish = { "old.code": "{n} eski", "new.code": "Yeni {other}" };
    expect(problemsOf(registry, english, turkish, frozen, ["en"])).toEqual([
      "Turkish new.code placeholders differ from [name]",
    ]);
  });

  it("still requires English for every code", () => {
    expect(
      problemsOf(registry, { "old.code": "{n} old" }, { "old.code": "{n} eski" }, frozen, ["en"]),
    ).toEqual(["English new.code is missing"]);
  });

  it("demands the Turkish twin of every code again once tr is enabled", () => {
    expect(
      problemsOf(registry, english, { "old.code": "{n} eski" }, frozen, ["en", "tr"]),
    ).toEqual(["Turkish new.code is missing"]);
  });

  it("covers the 21 qradar_health.reason codes of ARL-3 as new, untranslated codes", () => {
    const added = Object.fromEntries(
      Array.from({ length: 21 }, (_, i) => [`qradar_health.reason.r${i}`, { params: {} }]),
    );
    const text = Object.fromEntries(Object.keys(added).map((code) => [code, "reason"]));
    expect(
      problemsOf(
        { ...registry, ...added },
        { ...english, ...text },
        { "old.code": "{n} eski" },
        frozen,
        ["en"],
      ),
    ).toEqual([]);
  });
});
