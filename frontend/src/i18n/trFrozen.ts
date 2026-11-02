// The frozen Turkish catalog (D-41). Turkish is not deleted and not maintained: `messages/tr/`
// stays as it was at the tag `checkpoint/bilingual-2026-11-02` (14064d19), and `messages/en/`
// moves on. `tr-frozen-keys.json` lists every Turkish key at that tag. The Turkish contract
// tests use the three guards below instead of "tr has exactly the keys of en":
//   (a) Turkish keeps every frozen key (nothing is deleted by accident);
//   (b) Turkish has no key English lacks (no stale or misspelled key);
//   (c) every Turkish value present passes the same ICU, argument, typography and count checks.
// An English key added after the checkpoint needs no Turkish twin until Turkish is enabled
// again: while `ENABLED_LOCALES` holds `tr`, `parityProblems` also demands the twin.

import frozen from "./tr-frozen-keys.json";
import { ENABLED_LOCALES, type Locale } from "./locale";

/** A catalog value is text, an object, or (the guide's blocks) a list: lists flatten by index. */
export function flattenCatalog(
  catalog: object,
  prefix = "",
  out: Record<string, string> = {},
): Record<string, string> {
  for (const [key, value] of Object.entries(catalog)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else flattenCatalog(value, path, out);
  }
  return out;
}

export const FROZEN_TR_KEYS: readonly string[] = frozen.keys;

/** The problems of a Turkish catalog against the English one, empty when it is sound. */
export function parityProblems(
  english: Record<string, string>,
  turkish: Record<string, string>,
  enabled: readonly Locale[] = ENABLED_LOCALES,
  frozenKeys: readonly string[] = FROZEN_TR_KEYS,
): string[] {
  const problems: string[] = [];
  for (const key of frozenKeys) {
    if (!(key in turkish)) problems.push(`frozen Turkish key is missing: ${key}`);
  }
  for (const key of Object.keys(turkish)) {
    if (!(key in english)) problems.push(`Turkish key has no English twin: ${key}`);
  }
  if (enabled.includes("tr")) {
    for (const key of Object.keys(english)) {
      if (!(key in turkish)) problems.push(`English key has no Turkish twin: ${key}`);
    }
  }
  return problems.sort();
}
