import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";

// Turkish typography rules of the Language section (guidelines §Language), pinned over the whole
// Turkish catalog:
//   1. A sentence never starts with a lowercase letter, and never with a placeholder that holds a
//      lowercase noun (`{what}`, `{subject}`, `{reason}`): the message is restructured so it opens
//      with a fixed word ("Okunamadı: {what}.").
//   2. A number and its unit are never separated by an ordinary space: `24 sa`, `5 dk`, `7 gün`,
//      `{count} sn`, `{eps} EPS` use U+00A0.
//   3. A count of a total is `M / N <noun>` (U+00A0 around the slash), never a suffix on the number.

function flatten(node: unknown, path = ""): Array<[string, string]> {
  if (typeof node === "string") return [[path, node]];
  if (node && typeof node === "object") {
    return Object.entries(node).flatMap(([key, value]) => flatten(value, path ? `${path}.${key}` : key));
  }
  return [];
}

const turkish = flatten(tr);
const NOUN_ARGS = new Set(["what", "subject", "reason", "label", "noun"]);

/**
 * Keys that hold a fragment, not a sentence, though they end in a full stop or an ellipsis: the
 * two route-state sentences take a page or widget TITLE (capitalised by its own message), and the
 * `after` parts follow a time element inside one sentence.
 */
const FRAGMENTS = new Set<string>([
  "states.loading",
  "states.couldNotDisplay",
  "trend.stale.after",
  "trend.single.measuredAfter",
]);
/** The shell's page range (I18N-2): "1–50 / 200" with ordinary spaces, outside the Dashboard. */
const RANGE_KEYS = new Set<string>(["ui.pageRange"]);

describe("Turkish catalog typography", () => {
  it("never opens a sentence with a lowercase letter or a noun placeholder", () => {
    const failures = turkish
      .filter(([key, value]) => /[.…?]$/.test(value) && !FRAGMENTS.has(key))
      .filter(([, value]) => {
        const first = value.trimStart();
        if (first.startsWith("{")) return NOUN_ARGS.has(first.slice(1).split(/[,}]/)[0]);
        const letter = first.charAt(0);
        return letter !== letter.toLocaleUpperCase("tr-TR") && letter === letter.toLocaleLowerCase("tr-TR");
      })
      .map(([key, value]) => `${key}: ${value.slice(0, 50)}`);
    expect(failures).toEqual([]);
  });

  it("keeps a number and its unit together", () => {
    const failures = turkish
      .filter(([, value]) => /(?:[0-9}]) (?:sa|dk|sn|gün|EPS)(?![\p{L}])/u.test(value))
      .map(([key, value]) => `${key}: ${value.slice(0, 60)}`);
    expect(failures).toEqual([]);
  });

  it("writes a count of a total as M / N with no-break spaces", () => {
    const failures = turkish
      .filter(([key]) => !RANGE_KEYS.has(key))
      .filter(([, value]) => /\} (?:\/|içinden|tanesi)|\}'(?:dan|den|tan|ten)\b/.test(value) || /[0-9}] \/ [0-9{]/.test(value))
      .map(([key, value]) => `${key}: ${value.slice(0, 60)}`);
    expect(failures).toEqual([]);
  });

  it("has the same keys as English (sanity for the scan)", () => {
    expect(flatten(tr).length).toBe(flatten(en).length);
  });
});
