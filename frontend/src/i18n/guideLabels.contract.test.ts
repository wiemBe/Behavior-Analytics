import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";

// Every `**label**` the guide quotes is an on-screen label, so it must equal some value of that
// language's catalog (a placeholder such as `{name}` matches any placeholder). This is what makes
// the guide fail loudly when a later task renames a label. The guide's own namespaces are not a
// source of labels.

type Locale = "en" | "tr";
const CATALOGS = { en, tr } as const;
const GUIDE_NAMESPACES = new Set(["guide", "guideLinks"]);

/**
 * Every `**…**` span of a text, the text between the marks. A span that opens a paragraph and ends
 * with a colon is a run-in heading ("Order of decision:"), not a label on a screen.
 */
export function boldLabels(text: string): string[] {
  return [...text.matchAll(/\*\*([^*]+)\*\*/g)]
    .filter((match) => !(match.index === 0 && match[1].endsWith(":")))
    .map((match) => match[1]);
}

/** The text of every string in a value, lists and objects included. */
function strings(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (node && typeof node === "object") return Object.values(node).flatMap(strings);
  return [];
}

/** A text as characters and wildcards: a balanced `{…}` group (an ICU argument, plural or select) is one. */
type Token = string | null;
/**
 * Code marks do not count (the guide may quote a sentence with a name in code marks), and a tag
 * of a rich message is dropped. An ellipsis inside a label stands for the part the guide leaves
 * out. Case counts.
 */
function tokens(text: string): Token[] {
  const out: Token[] = [];
  let depth = 0;
  // An empty rich tag (`<time></time>`) is where the page puts a value: an argument.
  const plain = text.replace(/<(\w+)><\/\1>/g, "{x}").replace(/<[^>]*>/g, "").replace(/`/g, "");
  for (const char of plain) {
    if (char === "…" && depth === 0) {
      out.push(null);
      continue;
    }
    if (char === "{") {
      if (depth === 0) out.push(null);
      depth += 1;
    } else if (char === "}") {
      depth = Math.max(0, depth - 1);
    } else if (depth === 0) {
      out.push(char);
    }
  }
  return out;
}

/**
 * Whether a label quotes a catalog value: all of it, or (`start`) only its start, as the guide
 * does for a long sentence. A wildcard on either side stands for any non-empty run of the other
 * side (`24 widgets` against `{max} widgets`).
 */
export function quotes(label: string, value: string, start: boolean): boolean {
  const ellipsis = label.endsWith("…");
  const l = tokens(ellipsis ? label.slice(0, -1).trimEnd() : label);
  const v = tokens(value);
  const memo = new Map<number, number>();
  // An argument stands for a short run (a name, a number, a count), not for a clause: otherwise
  // "{count} sources" would accept any label that ends in "sources".
  const span = (next: (length: number) => number) => Math.max(-1, ...Array.from({ length: 12 }, (_, k) => next(k + 1)));
  // The most letters, digits and marks that match one-to-one, or -1 when the texts do not match.
  const walk = (i: number, j: number): number => {
    const key = i * (v.length + 1) + j;
    const known = memo.get(key);
    if (known !== undefined) return known;
    let result: number;
    if (i === l.length) result = start || ellipsis || j === v.length ? 0 : -1;
    else if (j === v.length) result = -1;
    else if (l[i] === null && v[j] === null) result = walk(i + 1, j + 1);
    else if (l[i] === null) result = span((k) => walk(i + 1, j + k));
    else if (v[j] === null) result = span((k) => walk(i + k, j + 1));
    else if (l[i] !== v[j]) result = -1;
    else {
      const rest = walk(i + 1, j + 1);
      result = rest < 0 ? -1 : rest + (l[i]!.trim() === "" ? 0 : 1);
    }
    memo.set(key, result);
    return result;
  };
  // A label must share real text with the value; matching only arguments and spaces is not a quote.
  const literal = l.filter((token) => token !== null && token.trim() !== "").length;
  return walk(0, 0) >= Math.min(3, literal);
}

export function catalogValues(locale: Locale): string[] {
  const catalog = CATALOGS[locale] as Record<string, unknown>;
  const values = new Set<string>();
  for (const [namespace, node] of Object.entries(catalog)) {
    if (GUIDE_NAMESPACES.has(namespace)) continue;
    // A value with no letter or digit of its own ("{count}", "{label}:") would match any label.
    for (const value of strings(node)) {
      if (tokens(value).some((token) => token !== null && /[\p{L}\p{N}]/u.test(token))) values.add(value);
    }
  }
  return [...values];
}

/** Whether some value of the catalog is quoted by the label. */
export function isQuoted(label: string, values: readonly string[]): boolean {
  // Cheap filter: a label starts with the same characters as the value it quotes, up to its first
  // placeholder or digit (a digit may stand where the value has an argument). A label that opens
  // with one, or a value that does, is checked in full.
  const head = (label.match(/^[^{\d]*/)?.[0] ?? "").slice(0, 8);
  return values.some(
    (value) => (head.length === 0 || value.startsWith("{") || value.startsWith(head)) && quotes(label, value, true),
  );
}

export function guideLabels(locale: Locale): Array<{ where: string; label: string }> {
  const sections = (CATALOGS[locale].guide as { sections: Record<string, unknown> }).sections;
  const found: Array<{ where: string; label: string }> = [];
  const visit = (node: unknown, where: string) => {
    if (typeof node === "string") {
      for (const label of boldLabels(node)) found.push({ where, label });
    } else if (node && typeof node === "object") {
      for (const [key, value] of Object.entries(node)) visit(value, `${where}.${key}`);
    }
  };
  for (const [id, section] of Object.entries(sections)) visit(section, id);
  return found;
}

/**
 * Labels the guide quotes that no catalog holds, because the backend, a schema or the data writes
 * them (GUIDE-1b labels table, rows `backend`, `schema` and `data`). English text on a Turkish
 * page until the backend sends message codes (D-37 phase 2). Each entry is the label as the
 * guide writes it in that language.
 */
const WRITTEN_ELSEWHERE: Record<Locale, ReadonlySet<string>> = {
  en: new Set([
    "NO_EVENTS on", // alert title, backend/app/anomaly/engine.py
    "URL path", // WAF schema field labels, backend/app/waf/profile.py
    "HTTP method",
    "HTTP status",
    "WAF rule id",
    "User agent",
    "Cisco Firepower Threat Defense", // IPS preset labels from the API, backend/app/api/routes/ips.py
    "Palo Alto THREAT",
    "NOT_COLLECTED", // the state code the API sends; the label is "Not collected"
    "No, history is not read", // the value part of history.line.no, which the page prints after a label
  ]),
  tr: new Set([
    "NO_EVENTS on",
    "URL path",
    "HTTP method",
    "HTTP status",
    "WAF rule id",
    "User agent",
    "Action", // WAF and IPS schema field labels, English on a Turkish page until D-37 phase 2
    "Source IP",
    "Destination IP",
    "Destination port",
    "Signature",
    "Protocol",
    "Severity",
    "Palo Alto THREAT",
    "Hayır, geçmiş okunmuyor",
  ]),
};

describe("guide labels stay true to the catalog", () => {
  for (const locale of ["en", "tr"] as const) {
    it(`quotes only ${locale} catalog values (or a reviewed backend/schema/data label)`, () => {
      const values = catalogValues(locale);
      const stray = guideLabels(locale).filter(
        ({ label }) => !WRITTEN_ELSEWHERE[locale].has(label) && !isQuoted(label, values),
      );
      expect(stray.map(({ where, label }) => `${where}: ${label}`)).toEqual([]);
    });
  }
});

describe("the label matcher", () => {
  it("takes a placeholder on either side for any text", () => {
    expect(quotes("Over 24h SLA", "Over {hours}h SLA", false)).toBe(true);
    expect(quotes("Save roles for {name}", "Save roles for {user}", false)).toBe(true);
    expect(quotes("{name} için sıklık", "{name} için sıklık", false)).toBe(true);
  });

  it("accepts the start of a long sentence only when asked, and an ellipsis for the part left out", () => {
    expect(quotes("No rules collected yet.", "No rules collected yet. Run a rule sync.", false)).toBe(false);
    expect(quotes("No rules collected yet.", "No rules collected yet. Run a rule sync.", true)).toBe(true);
    expect(quotes("Last step failed at … (…). It retries.", "Last step failed at <time></time> ({e}). It retries.", false)).toBe(true);
  });

  it("does not accept a renamed label, a longer label or another case", () => {
    expect(quotes("Observed", "Gözlenen", true)).toBe(false);
    expect(quotes("Observed rate", "Observed", true)).toBe(false);
    expect(quotes("observed", "Observed", true)).toBe(false);
  });

  it("finds the catalog's own labels and rejects an invented one", () => {
    const values = catalogValues("en");
    expect(isQuoted("Observed", values)).toBe(true);
    expect(isQuoted("Quantum flux label", values)).toBe(false);
    expect(isQuoted("Zzz renamed label", values)).toBe(false);
  });

  it("reads bold spans, and skips a run-in heading", () => {
    expect(boldLabels("Open **Users**, then **Create account**.")).toEqual(["Users", "Create account"]);
    expect(boldLabels("**Order of decision:** **Operator override** first.")).toEqual(["Operator override"]);
    expect(boldLabels("A **note:** inside stays a label.")).toEqual(["note:"]);
  });
});
