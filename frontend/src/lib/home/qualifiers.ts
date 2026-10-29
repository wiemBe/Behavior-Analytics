
import { formatCount } from "@/lib/format";

// The caveats a Home widget attaches to data it does have (UR.5.4). They are
// qualifiers on an ok state, not states of their own: the numbers beside them
// are real, and the qualifier says how far to trust them. A derivation returns
// its value and these together, so a widget cannot draw one without the other.

export type Qualifier =
  /** Collected, but not enough to judge. Neutral, never green. */
  | { kind: "INSUFFICIENT_DATA"; reason: string }
  /** `have` of `of` units were collected; what is shown covers only those. */
  | { kind: "PARTIAL"; have: number; of: number; unit: string; text: string }
  /** A capped list: `shown` of `total` rows were read. */
  | { kind: "TRUNCATED"; shown: number; total: number; text: string }
  /** The source has not produced anything yet. Not zero. */
  | { kind: "NOT_COLLECTED"; reason: string };

const n = (value: number) => formatCount(value);

/**
 * Why a derivation has nothing to show, as a code with its values. The components translate the
 * code; the English `reason` stays on the qualifier for callers that print it as it is. The code
 * rides beside the object (a side table), not in it, because callers compare qualifiers
 * structurally.
 */
export type ReasonCode =
  | "NO_CLASS_COMPLETE"
  | "NO_STEP"
  | "NO_STEP_COMPLETE"
  | "NO_MONITORED"
  | "NO_VERDICT"
  | "NO_SNAPSHOT"
  | "SNAPSHOT_UNCONFIRMED"
  | "INVALID_RANGE";

export interface ReasonInfo {
  code: ReasonCode;
  values?: Record<string, number>;
}

const REASONS = new WeakMap<object, ReasonInfo>();
const WORDED = new WeakSet<object>();

/** The code (and values) a derivation gave a qualifier, or null for a qualifier worded elsewhere. */
export function reasonInfo(q: Qualifier): ReasonInfo | null {
  return REASONS.get(q) ?? null;
}

/**
 * Marks a qualifier whose `text` a widget already wrote in the interface language, so the frame
 * shows that text as it is. Every other PARTIAL / TRUNCATED qualifier is worded from its values.
 */
export function worded<Q extends Qualifier>(q: Q): Q {
  WORDED.add(q);
  return q;
}

export function isWorded(q: Qualifier): boolean {
  return WORDED.has(q);
}

export function insufficient(reason: string, code?: ReasonInfo): Qualifier {
  const q: Qualifier = { kind: "INSUFFICIENT_DATA", reason };
  if (code) REASONS.set(q, code);
  return q;
}

export function notCollected(reason: string, code?: ReasonInfo): Qualifier {
  const q: Qualifier = { kind: "NOT_COLLECTED", reason };
  if (code) REASONS.set(q, code);
  return q;
}

/** "270 of 288 intervals collected" */
export function partial(have: number, of: number, unit: string): Qualifier {
  return { kind: "PARTIAL", have, of, unit, text: `${n(have)} of ${n(of)} ${unit} collected` };
}

/** "First 200 of 345" */
export function truncated(shown: number, total: number): Qualifier {
  return { kind: "TRUNCATED", shown, total, text: `First ${n(shown)} of ${n(total)}` };
}

/**
 * One short phrase for a widget's header pill: the worst caveat, in words. A state that
 * leaves nothing to draw (not collected, insufficient) outranks one that qualifies what is
 * drawn (truncated, partial). Null when there is no caveat.
 */
export function badgeText(qualifiers: Qualifier[]): string | null {
  const find = (kind: Qualifier["kind"]) => qualifiers.find((q) => q.kind === kind);
  if (find("NOT_COLLECTED")) return "Not collected";
  if (find("INSUFFICIENT_DATA")) return "Insufficient data";
  const t = find("TRUNCATED");
  if (t && t.kind === "TRUNCATED") return `Truncated: ${t.text.charAt(0).toLowerCase()}${t.text.slice(1)}`;
  const p = find("PARTIAL");
  if (p && p.kind === "PARTIAL") return `Partial: ${p.text}`;
  return null;
}
