// The words of the honesty qualifiers (UR.5.4) in the interface language.
//
// `lib/home/qualifiers.ts` builds each qualifier with English text. The structure travels with it
// (the kind, the counts, the unit), so the pill and the standard sentences are rebuilt here from
// the structure, never by reading the text. A sentence the structure cannot rebuild (a widget's own
// wording, passed already translated) is shown as it is.
//
// A reason travels as a code with its values (`reasonInfo`, from the derivation that wrote it) and
// is translated by the code: `home.reasons.<code>`. A qualifier a widget worded itself (already in
// the interface language, with no code) is shown as it is. Text is never matched.

import type { TextTranslator } from "@/i18n/textTranslator";
import type { useMeasures } from "@/i18n/measures";
import { isWorded, reasonInfo, type Qualifier, type ReasonCode } from "@/lib/home/qualifiers";
import { own } from "@/i18n/ownKey";

type Measures = ReturnType<typeof useMeasures>;
type Say = (key: string, values?: Record<string, string | number>) => string;

const PILL_KEY: Record<Qualifier["kind"], string> = {
  INSUFFICIENT_DATA: "insufficientData",
  PARTIAL: "partial",
  TRUNCATED: "truncated",
  NOT_COLLECTED: "notCollected",
};

/** The pill word of a qualifier; `states` is the translator of the `states` namespace. */
export function qualifierPill(states: Say, kind: Qualifier["kind"]): string {
  return states(PILL_KEY[kind]);
}

/** The units the derivations count in, by the text they pass. */
const UNIT_KEY: Record<string, string> = {
  intervals: "intervals",
  steps: "steps",
  "stored buckets": "storedBuckets",
  "five-minute intervals": "fiveMinuteIntervals",
};

/** The catalog key (`home.reasons.<key>`) of each reason code. */
const REASON_KEY: Record<ReasonCode, string> = {
  NO_CLASS_COMPLETE: "noClassComplete",
  NO_STEP: "noStep",
  NO_STEP_COMPLETE: "noStepComplete",
  NO_MONITORED: "noMonitored",
  NO_VERDICT: "noVerdict",
  NO_SNAPSHOT: "noSnapshot",
  SNAPSHOT_UNCONFIRMED: "snapshotUnconfirmed",
  INVALID_RANGE: "invalidRange",
};

/** The reason of an INSUFFICIENT_DATA / NOT_COLLECTED qualifier in the interface language. */
export function reasonText(say: TextTranslator, q: Qualifier): string {
  if (q.kind !== "INSUFFICIENT_DATA" && q.kind !== "NOT_COLLECTED") return "";
  const info = reasonInfo(q);
  return info ? say(`home.reasons.${REASON_KEY[info.code]}`, info.values) : q.reason;
}

/** The sentence of a qualifier: `compact` is the KPI card's short form of PARTIAL. */
export function qualifierText(
  say: TextTranslator,
  measures: Measures,
  q: Qualifier,
  compact: boolean,
): string {
  if (q.kind === "INSUFFICIENT_DATA" || q.kind === "NOT_COLLECTED") {
    return reasonText(say, q);
  }
  if (q.kind === "PARTIAL") {
    if (isWorded(q)) return q.text;
    const unitKey = own(UNIT_KEY, q.unit);
    const unit = unitKey ? say(`home.qualifier.unit.${unitKey}`) : q.unit;
    const values = { have: measures.count(q.have), of: measures.count(q.of), unit };
    return say(compact ? "home.qualifier.partialCompact" : "home.qualifier.partial", values);
  }
  if (isWorded(q)) return q.text;
  return say("home.qualifier.firstOf", {
    shown: measures.count(q.shown),
    total: measures.count(q.total),
  });
}

/** The header pill phrase of a widget: the worst caveat in words, or null (see `badgeText`). */
export function badgePhrase(
  say: TextTranslator,
  measures: Measures,
  qualifiers: Qualifier[],
): string | null {
  const find = (kind: Qualifier["kind"]) => qualifiers.find((q) => q.kind === kind);
  if (find("NOT_COLLECTED")) return say("states.notCollected");
  if (find("INSUFFICIENT_DATA")) return say("states.insufficientData");
  const t = find("TRUNCATED");
  if (t && t.kind === "TRUNCATED") {
    return say("home.qualifier.truncatedBadge", { text: lowerFirst(qualifierText(say, measures, t, false), measures.locale) });
  }
  const p = find("PARTIAL");
  if (p && p.kind === "PARTIAL") {
    return say("home.qualifier.partialBadge", { text: qualifierText(say, measures, p, false) });
  }
  return null;
}

function lowerFirst(text: string, locale: string): string {
  return `${text.charAt(0).toLocaleLowerCase(locale === "tr" ? "tr-TR" : "en-US")}${text.slice(1)}`;
}
