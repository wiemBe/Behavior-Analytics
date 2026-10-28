import { isPlainKey } from "@/i18n/ownKey";
import type { TextTranslator } from "@/i18n/textTranslator";

/** A rule dependency's group word (`mappingSources`, `dependencyKinds`); an unknown value as sent. */
export function dependencyWord(
  say: TextTranslator,
  group: "mappingSources" | "dependencyKinds",
  value: string,
): string {
  const key = `rules.detail.${group}.${value}`;
  return isPlainKey(value) && say.has?.(key) ? say(key) : value;
}
