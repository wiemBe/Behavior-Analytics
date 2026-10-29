// The words of the coverage and rule-health statuses, and of the coverage mappings, by value.
//
// The API sends codes (`NOT_EVALUATED`, `LOG_SOURCE`, `EXPLICIT`); the interface shows them as the
// catalog words (`coverage.status`, `coverage.kind`, `coverage.source`). A value the catalog does
// not know is shown as the English did before: the code with underscores read as spaces (and the
// mapping source in lower case). It is never an empty string.

import type { TextTranslator } from "@/i18n/textTranslator";
import { isPlainKey } from "@/i18n/ownKey";

function lookup(say: TextTranslator, group: string, value: string): string | null {
  const key = `coverage.${group}.${value}`;
  return isPlainKey(value) && say.has?.(key) ? say(key) : null;
}

/** `NOT_EVALUATED` is "NOT EVALUATED" in English; also the rule-health verdicts. */
export function statusLabel(say: TextTranslator, status: string): string {
  return lookup(say, "status", status) ?? status.replace(/_/g, " ");
}

/** The kind of a data-source dependency (`LOG_SOURCE_TYPE`). */
export function kindLabel(say: TextTranslator, kind: string): string {
  return lookup(say, "kind", kind) ?? kind.replace(/_/g, " ");
}

/** Where a mapping came from (`EXPLICIT` is "explicit"); lower case, as the English printed it. */
export function mappingSourceLabel(say: TextTranslator, source: string): string {
  return lookup(say, "source", source.toLowerCase()) ?? source.toLowerCase();
}
