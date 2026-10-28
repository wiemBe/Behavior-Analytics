// Presentation rules for the Phase C log-source fleet.
//
// The rule this file exists to enforce is the one the rest of the product
// already lives by: a class with no complete measurement behind it is "not
// measured", never 0 and never 0 EPS. The API sends null for such a figure;
// every formatter here keeps null apart from a measured zero.

import {
  ApiError,
  actionErrorMessage,
  type ClassificationBasis,
  type ClassificationConflict,
  type ClassificationReviewStatus,
  type ClassificationRule,
  type ClassificationRuleKind,
  type FleetBucketCounts,
  type FleetClassSummary,
  type FleetSource,
  type FleetSourceParams,
  type FleetWindow,
  type SourceClass,
} from "@/lib/api";

export type { FleetWindow };
import { detectorLabel, NOT_MEASURED } from "@/lib/behavior";
import type { FormatLocale } from "@/lib/format";
import { formatCount } from "@/lib/format";
import { englishFleetText, plainCount, type FleetText } from "@/lib/fleetText";
import { isPlainKey, own } from "@/i18n/ownKey";

// --- classes ------------------------------------------------------------------

export const CLASS_LABELS: Record<SourceClass, string> = {
  WAF: "WAF",
  IPS: "IPS",
  FIREWALL: "Firewall",
  PROXY: "Proxy",
  VPN: "VPN",
  LINUX: "Linux",
  WINDOWS: "Windows",
  NETWORK: "Network",
  DNS: "DNS",
  DATABASE: "Database",
  EMAIL: "Email",
  ENDPOINT: "Endpoint",
  CLOUD: "Cloud",
  IDENTITY: "Identity",
  APPLICATION: "Application",
  QRADAR_INTERNAL: "QRadar internal",
  OTHER: "Other",
  UNCLASSIFIED: "Unclassified",
};

export const ALL_CLASSES = Object.keys(CLASS_LABELS) as SourceClass[];

/** What an operator may assign: UNCLASSIFIED is what clearing the override gives. */
export const ASSIGNABLE_CLASSES = ALL_CLASSES.filter((c) => c !== "UNCLASSIFIED");

/**
 * The words of the catalog by value, or the value itself when the catalog has no message for it
 * (a value this build does not know is still information).
 */
function labelOf(text: FleetText, group: string, value: string, english?: string): string {
  const key = `${group}.${value}`;
  if (isPlainKey(value) && text.say.has?.(key)) return text.say(key);
  return english ?? value;
}

export function classLabel(value: string | null | undefined, text: FleetText = englishFleetText): string {
  if (value == null) return NOT_MEASURED;
  return labelOf(text, "enums.sourceClass", value, own(CLASS_LABELS, value) ?? value);
}

/** Tone of a class pill. Only UNCLASSIFIED is flagged: it needs a decision. */
export function classTone(value: SourceClass): "warn" | "" {
  return value === "UNCLASSIFIED" ? "warn" : "";
}

// --- window ---------------------------------------------------------------------

export const FLEET_WINDOWS: { value: FleetWindow; label: string }[] = [
  { value: "1h", label: "1 h" },
  { value: "24h", label: "24 h" },
  { value: "7d", label: "7 d" },
];

export const DEFAULT_WINDOW: FleetWindow = "24h";

export function parseWindow(value: string | undefined): FleetWindow {
  return FLEET_WINDOWS.some((w) => w.value === value) ? (value as FleetWindow) : DEFAULT_WINDOW;
}

/** Dashboard fleet widgets keep the window the analyst was looking at. */
export function fleetDrillHref(window: FleetWindow): string {
  return `/fleet?window=${window}`;
}

export function windowName(window: FleetWindow, text: FleetText = englishFleetText): string {
  return text.say(`fleet.window.name.${window}`);
}

// --- the figures of one class -----------------------------------------------------

export const NOT_MEASURED_TEXT = "Not measured";

/** EPS to two decimals; a measured 0 reads "0.00", a missing value reads null. */
export function formatEpsValue(
  value: number | null | undefined,
  locale: FormatLocale = "en",
): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return value.toLocaleString(locale === "tr" ? "tr-TR" : "en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatEventCount(
  value: number | null | undefined,
  locale: FormatLocale = "en",
): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return value.toLocaleString(locale === "tr" ? "tr-TR" : "en-US");
}

export interface CompletenessInfo {
  state: "none" | "complete" | "incomplete";
  /** "270 of 288 buckets complete" */
  text: string;
  /** "10 partial, 8 missing" when incomplete. */
  detail: string | null;
}

/**
 * Completeness of the window for a class, counted in buckets, not rows.
 * A class with no expected buckets has no monitored source: that is its own
 * state, not "100% complete".
 */
export function completenessInfo(
  b: FleetBucketCounts,
  fleet: FleetText = englishFleetText,
): CompletenessInfo {
  const { say, locale } = fleet;
  if (b.expected <= 0) {
    return { state: "none", text: say("fleet.completeness.none"), detail: null };
  }
  const text = say("fleet.completeness.text", {
    complete: formatCount(b.complete, locale),
    expected: formatCount(b.expected, locale),
  });
  if (b.complete >= b.expected && b.partial === 0 && b.missing === 0) {
    return { state: "complete", text, detail: null };
  }
  return {
    state: "incomplete",
    text,
    detail: say("fleet.completeness.detail", {
      partial: formatCount(b.partial, locale),
      missing: formatCount(b.missing, locale),
    }),
  };
}

export interface ExpectedInfo {
  /** Null when no source in the class has a baseline. */
  value: string | null;
  note: string;
  /** True when the expectation covers only part of the class. */
  partial: boolean;
}

/**
 * The expected EPS with its basis. The expectation is the sum of the baselined
 * sources only, so when some sources have none it understates the class and
 * must not be compared with the mean as if it covered all of it.
 */
export function expectedInfo(c: FleetClassSummary, fleet: FleetText = englishFleetText): ExpectedInfo {
  const { say, locale } = fleet;
  const value = formatEpsValue(c.expected_eps, locale);
  if (value === null) {
    return {
      value: null,
      note: say("fleet.expected.noBaseline"),
      partial: false,
    };
  }
  const share = c.baseline_share;
  if (share == null || share <= 0) {
    return {
      value,
      note: say("fleet.expected.from", { count: plainCount(c.baseline_sources, locale) }),
      partial: false,
    };
  }
  const monitored = Math.round(c.baseline_sources / share);
  const partial = c.baseline_sources < monitored;
  return {
    value,
    note: say("fleet.expected.fromOf", {
      count: plainCount(c.baseline_sources, locale),
      total: plainCount(monitored, locale),
    }),
    partial,
  };
}

/** Open Phase A incidents as `[label, count]`, most numerous first. */
export function openIncidentList(
  c: FleetClassSummary,
  text: FleetText = englishFleetText,
): [string, number][] {
  return Object.entries(c.open_incidents)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type, n]) => [labelOf(text, "enums.anomalyType", type, detectorLabel(type)), n]);
}

/** Whether the class has sources to measure at all. */
export function hasSources(c: FleetClassSummary): boolean {
  return c.sources.enabled + c.sources.disabled > 0;
}

// --- classification, in words -------------------------------------------------------

/** The English wording; the catalog (`fleet.basis`) carries both languages. */
const BASIS_LABELS: Record<string, string> = {
  OVERRIDE: "Operator override",
  INTERNAL: "QRadar internal",
  UNCLASSIFIED: "No rule matched",
  GROUP: "Group rule",
  TYPE: "Type rule",
  TYPE_NAME_REGEX: "Type-name rule",
  NAME_REGEX: "Name rule",
  IDENTIFIER_REGEX: "Identifier rule",
};

export function basisLabel(basis: string, text: FleetText = englishFleetText): string {
  return labelOf(text, "fleet.basis", basis, own(BASIS_LABELS, basis) ?? basis);
}

/** The English wording; the catalog (`fleet.conflict.subject`) carries both languages. */
const BASIS_SUBJECT: Record<string, string> = {
  OVERRIDE: "override",
  INTERNAL: "QRadar",
  GROUP: "group",
  TYPE: "type",
  TYPE_NAME_REGEX: "type",
  NAME_REGEX: "name",
  IDENTIFIER_REGEX: "identifier",
};

function says(basis: string | null, cls: string | null, text: FleetText): string | null {
  if (basis == null || cls == null) return null;
  const subject = BASIS_SUBJECT[basis];
  if (!subject) return null;
  return text.say("fleet.conflict.says", {
    subject: labelOf(text, "fleet.conflict.subject", basis, subject),
    class: classLabel(cls, text),
  });
}

/**
 * The disagreement behind a conflict flag, as one sentence fragment:
 * "type says IPS, name says Linux". The winner's own basis comes first; a
 * conflict entry repeating it is dropped. Empty when there is no conflict.
 */
export function conflictText(
  source: Pick<FleetSource, "basis" | "source_class" | "conflicts">,
  text: FleetText = englishFleetText,
): string {
  if (source.conflicts.length === 0) return "";
  const parts: string[] = [];
  const winner = says(source.basis, source.source_class, text);
  if (winner && source.source_class !== "UNCLASSIFIED") parts.push(winner);
  for (const c of source.conflicts as ClassificationConflict[]) {
    const part = says(c.basis, c.class, text);
    if (part && !parts.includes(part)) parts.push(part);
  }
  return parts.join(", ");
}

export function reviewLabel(
  status: ClassificationReviewStatus | null,
  text: FleetText = englishFleetText,
): string {
  if (status === "NEW") return text.say("fleet.review.NEW");
  if (status === "REVIEWED") return text.say("fleet.review.REVIEWED");
  return text.say("fleet.review.notRecorded");
}

export function originLabel(autoDiscovered: boolean | null, text: FleetText = englishFleetText): string {
  if (autoDiscovered == null) return text.say("fleet.origin.notRecorded");
  return autoDiscovered ? text.say("fleet.origin.auto") : text.say("fleet.origin.configured");
}

// --- the source list's query ----------------------------------------------------------

export const FLEET_SOURCE_PAGE_SIZE = 50;

const REVIEW_STATUSES: ClassificationReviewStatus[] = ["NEW", "REVIEWED"];
const BASES: ClassificationBasis[] = [
  "OVERRIDE",
  "INTERNAL",
  "UNCLASSIFIED",
  "GROUP",
  "TYPE",
  "TYPE_NAME_REGEX",
  "NAME_REGEX",
  "IDENTIFIER_REGEX",
];

export const BASIS_OPTIONS = BASES;

export interface FleetSourceQuery {
  class: SourceClass | "";
  review_status: ClassificationReviewStatus | "";
  has_conflict: "true" | "false" | "";
  basis: ClassificationBasis | "";
  offset: number;
}

/** Every value arrives from a URL a third party may have written: allow-list it. */
export function parseFleetSourceQuery(
  raw: Record<string, string | undefined>,
): FleetSourceQuery {
  const offset = Number(raw.offset);
  return {
    class: ALL_CLASSES.includes(raw.class as SourceClass) ? (raw.class as SourceClass) : "",
    review_status: REVIEW_STATUSES.includes(raw.review_status as ClassificationReviewStatus)
      ? (raw.review_status as ClassificationReviewStatus)
      : "",
    has_conflict: raw.has_conflict === "true" || raw.has_conflict === "false" ? raw.has_conflict : "",
    basis: BASES.includes(raw.basis as ClassificationBasis) ? (raw.basis as ClassificationBasis) : "",
    offset: Number.isInteger(offset) && offset > 0 ? Math.min(offset, 1_000_000) : 0,
  };
}

export function isFleetFiltered(q: FleetSourceQuery): boolean {
  return Boolean(q.class || q.review_status || q.has_conflict || q.basis);
}

/** The filters as URL parameters, for pagination links. */
export function fleetQueryParams(q: FleetSourceQuery): Record<string, string | undefined> {
  return {
    class: q.class || undefined,
    review_status: q.review_status || undefined,
    has_conflict: q.has_conflict || undefined,
    basis: q.basis || undefined,
  };
}

export function fleetApiParams(q: FleetSourceQuery): FleetSourceParams {
  return {
    class: q.class || undefined,
    review_status: q.review_status || undefined,
    has_conflict: q.has_conflict === "" ? undefined : q.has_conflict === "true",
    basis: q.basis || undefined,
    limit: FLEET_SOURCE_PAGE_SIZE,
    offset: q.offset,
  };
}

/** The active filters as readable chips: "Class: Linux". */
export function activeFilterLabels(q: FleetSourceQuery, text: FleetText = englishFleetText): string[] {
  const { say } = text;
  const labels: string[] = [];
  if (q.class) labels.push(say("fleet.chips.class", { value: classLabel(q.class, text) }));
  if (q.review_status) {
    labels.push(say("fleet.chips.review", { value: reviewLabel(q.review_status, text) }));
  }
  if (q.has_conflict) {
    labels.push(say(q.has_conflict === "true" ? "fleet.chips.conflicting" : "fleet.chips.noConflict"));
  }
  if (q.basis) labels.push(say("fleet.chips.basis", { value: basisLabel(q.basis, text) }));
  return labels;
}

// --- classification rules ----------------------------------------------------------------

/** The precedence, highest first. Stated once on the rules page (design C.3). English; see `precedenceSteps`. */
export const PRECEDENCE: { title: string; detail: string }[] = [
  { title: "Operator override", detail: "the class chosen for one source" },
  { title: "QRadar internal", detail: "the type is internal, or the identifier is 127.0.0.1" },
  { title: "Group rules", detail: "a group the source is in, or any ancestor of it" },
  { title: "Type rules", detail: "the numeric type id, or a pattern on the type name" },
  { title: "Name rules", detail: "a pattern on the source name or its identifier" },
  { title: "Unclassified", detail: "nothing matched" },
];

/** The catalog key (`classification.precedence.<key>`) of each step of `PRECEDENCE`, in order. */
const PRECEDENCE_KEYS = ["override", "internal", "group", "type", "name", "unclassified"];

/** `PRECEDENCE` in the interface language: the same steps, the same order. */
export function precedenceSteps(
  text: FleetText = englishFleetText,
): { key: string; title: string; detail: string }[] {
  return PRECEDENCE_KEYS.map((key) => ({
    key,
    title: text.say(`classification.precedence.${key}.title`),
    detail: text.say(`classification.precedence.${key}.detail`),
  }));
}

export interface RuleLevel {
  id: "group" | "type" | "name";
  title: string;
  kinds: ClassificationRuleKind[];
}

export const RULE_LEVELS: RuleLevel[] = [
  { id: "group", title: "Group rules", kinds: ["GROUP"] },
  { id: "type", title: "Type rules", kinds: ["TYPE", "TYPE_NAME_REGEX"] },
  { id: "name", title: "Name rules", kinds: ["NAME_REGEX", "IDENTIFIER_REGEX"] },
];

export const RULE_KIND_LABELS: Record<ClassificationRuleKind, string> = {
  GROUP: "Group id",
  TYPE: "Type id",
  TYPE_NAME_REGEX: "Type name pattern",
  NAME_REGEX: "Source name pattern",
  IDENTIFIER_REGEX: "Identifier pattern",
};

export const RULE_KINDS = Object.keys(RULE_KIND_LABELS) as ClassificationRuleKind[];

/** A rule level's title in the interface language (`RuleLevel.title` is the English one). */
export function ruleLevelTitle(level: RuleLevel, text: FleetText = englishFleetText): string {
  return text.say(`classification.level.${level.id}`);
}

/** A rule kind's words; a kind this build does not know is shown as sent. */
export function ruleKindLabel(kind: string, text: FleetText = englishFleetText): string {
  return labelOf(text, "classification.kind", kind, RULE_KIND_LABELS[kind as ClassificationRuleKind] ?? kind);
}

export function isRegexKind(kind: ClassificationRuleKind): boolean {
  return kind !== "GROUP" && kind !== "TYPE";
}

export function patternHint(
  kind: ClassificationRuleKind,
  text: FleetText = englishFleetText,
): string {
  return text.say(isRegexKind(kind) ? "classification.hint.regex" : "classification.hint.numeric");
}

/** The order the API lists rules in: priority descending, then kind, then pattern. */
export function sortRules(rules: ClassificationRule[]): ClassificationRule[] {
  return [...rules].sort(
    (a, b) =>
      b.priority - a.priority ||
      a.kind.localeCompare(b.kind) ||
      a.pattern.localeCompare(b.pattern) ||
      a.id.localeCompare(b.id),
  );
}

export function rulesAtLevel(rules: ClassificationRule[], level: RuleLevel): ClassificationRule[] {
  return sortRules(rules.filter((r) => level.kinds.includes(r.kind)));
}

export type RuleFailure = {
  kind: "forbidden" | "error";
  text: string;
  /** The form field the message belongs to, when it names one. */
  field: "pattern" | null;
};

/** Messages the API words about the pattern (validation, or a duplicate). */
export function concernsPattern(message: string): boolean {
  return /pattern|regular expression|numeric id|already exists/i.test(message);
}

/**
 * An answer for a rejected rule write. A 422 or 409 carries the API's own
 * sentence, which says what to correct, so it is shown as sent; a 403 names the
 * permission; anything else degrades to the shared safe messages.
 */
export function ruleFailure(err: unknown, text: FleetText = englishFleetText): RuleFailure {
  if (err instanceof ApiError) {
    if (err.status === 403) {
      return { kind: "forbidden", text: text.say("classification.failure.forbidden"), field: null };
    }
    if ((err.status === 422 || err.status === 409) && err.message) {
      return {
        kind: "error",
        text: err.message,
        field: concernsPattern(err.message) ? "pattern" : null,
      };
    }
  }
  return {
    kind: "error",
    text: text.say("classification.failure.notChanged", {
      reason: actionErrorMessage(err, text.say),
    }),
    field: null,
  };
}

/** The same for a source write (override, review). */
export function sourceFailure(
  err: unknown,
  text: FleetText = englishFleetText,
): { kind: "forbidden" | "error"; text: string } {
  if (err instanceof ApiError && err.status === 403) {
    return { kind: "forbidden", text: text.say("fleet.failure.sourceForbidden") };
  }
  return {
    kind: "error",
    text: text.say("fleet.failure.notChanged", { reason: actionErrorMessage(err, text.say) }),
  };
}
