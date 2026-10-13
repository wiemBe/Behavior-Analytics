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
import { detectorLabel, NOT_MEASURED } from "@/lib/behavior";

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

export function classLabel(value: string | null | undefined): string {
  if (value == null) return NOT_MEASURED;
  return CLASS_LABELS[value as SourceClass] ?? value;
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

export function windowName(window: FleetWindow): string {
  return { "1h": "1 hour", "24h": "24 hours", "7d": "7 days" }[window];
}

// --- the figures of one class -----------------------------------------------------

export const NOT_MEASURED_TEXT = "Not measured";

/** EPS to two decimals; a measured 0 reads "0.00", a missing value reads null. */
export function formatEpsValue(value: number | null | undefined): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatEventCount(value: number | null | undefined): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return value.toLocaleString("en-US");
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
export function completenessInfo(b: FleetBucketCounts): CompletenessInfo {
  if (b.expected <= 0) {
    return { state: "none", text: "No monitored sources", detail: null };
  }
  const text = `${b.complete.toLocaleString("en-US")} of ${b.expected.toLocaleString("en-US")} buckets complete`;
  if (b.complete >= b.expected && b.partial === 0 && b.missing === 0) {
    return { state: "complete", text, detail: null };
  }
  return {
    state: "incomplete",
    text,
    detail: `${b.partial.toLocaleString("en-US")} partial, ${b.missing.toLocaleString("en-US")} missing`,
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
export function expectedInfo(c: FleetClassSummary): ExpectedInfo {
  const value = formatEpsValue(c.expected_eps);
  if (value === null) {
    return {
      value: null,
      note: "No source has a baseline yet.",
      partial: false,
    };
  }
  const share = c.baseline_share;
  if (share == null || share <= 0) {
    return { value, note: `From ${c.baseline_sources} sources with a baseline.`, partial: false };
  }
  const monitored = Math.round(c.baseline_sources / share);
  const partial = c.baseline_sources < monitored;
  return {
    value,
    note: `From ${c.baseline_sources} of ${monitored} sources with a baseline.`,
    partial,
  };
}

/** Open Phase A incidents as `[label, count]`, most numerous first. */
export function openIncidentList(c: FleetClassSummary): [string, number][] {
  return Object.entries(c.open_incidents)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type, n]) => [detectorLabel(type), n]);
}

/** Whether the class has sources to measure at all. */
export function hasSources(c: FleetClassSummary): boolean {
  return c.sources.enabled + c.sources.disabled > 0;
}

// --- classification, in words -------------------------------------------------------

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

export function basisLabel(basis: string): string {
  return BASIS_LABELS[basis] ?? basis;
}

const BASIS_SUBJECT: Record<string, string> = {
  OVERRIDE: "override",
  INTERNAL: "QRadar",
  GROUP: "group",
  TYPE: "type",
  TYPE_NAME_REGEX: "type",
  NAME_REGEX: "name",
  IDENTIFIER_REGEX: "identifier",
};

function says(basis: string | null, cls: string | null): string | null {
  if (basis == null || cls == null) return null;
  const subject = BASIS_SUBJECT[basis];
  return subject ? `${subject} says ${classLabel(cls)}` : null;
}

/**
 * The disagreement behind a conflict flag, as one sentence fragment:
 * "type says IPS, name says Linux". The winner's own basis comes first; a
 * conflict entry repeating it is dropped. Empty when there is no conflict.
 */
export function conflictText(source: Pick<FleetSource, "basis" | "source_class" | "conflicts">): string {
  if (source.conflicts.length === 0) return "";
  const parts: string[] = [];
  const winner = says(source.basis, source.source_class);
  if (winner && source.source_class !== "UNCLASSIFIED") parts.push(winner);
  for (const c of source.conflicts as ClassificationConflict[]) {
    const part = says(c.basis, c.class);
    if (part && !parts.includes(part)) parts.push(part);
  }
  return parts.join(", ");
}

export function reviewLabel(status: ClassificationReviewStatus | null): string {
  if (status === "NEW") return "New";
  if (status === "REVIEWED") return "Reviewed";
  return "Not recorded";
}

export function originLabel(autoDiscovered: boolean | null): string {
  if (autoDiscovered == null) return "Origin not recorded";
  return autoDiscovered ? "Auto-discovered" : "Configured";
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
export function activeFilterLabels(q: FleetSourceQuery): string[] {
  const labels: string[] = [];
  if (q.class) labels.push(`Class: ${classLabel(q.class)}`);
  if (q.review_status) labels.push(`Review: ${reviewLabel(q.review_status)}`);
  if (q.has_conflict) labels.push(q.has_conflict === "true" ? "Conflicting only" : "No conflict");
  if (q.basis) labels.push(`Basis: ${basisLabel(q.basis)}`);
  return labels;
}

// --- classification rules ----------------------------------------------------------------

/** The precedence, highest first. Stated once on the rules page (design C.3). */
export const PRECEDENCE: { title: string; detail: string }[] = [
  { title: "Operator override", detail: "the class chosen for one source" },
  { title: "QRadar internal", detail: "the type is internal, or the identifier is 127.0.0.1" },
  { title: "Group rules", detail: "a group the source is in, or any ancestor of it" },
  { title: "Type rules", detail: "the numeric type id, or a pattern on the type name" },
  { title: "Name rules", detail: "a pattern on the source name or its identifier" },
  { title: "Unclassified", detail: "nothing matched" },
];

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

export function isRegexKind(kind: ClassificationRuleKind): boolean {
  return kind !== "GROUP" && kind !== "TYPE";
}

export function patternHint(kind: ClassificationRuleKind): string {
  return isRegexKind(kind)
    ? "A regular expression, at most 256 characters."
    : "A numeric id.";
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
export function ruleFailure(err: unknown): RuleFailure {
  if (err instanceof ApiError) {
    if (err.status === 403) {
      return {
        kind: "forbidden",
        text: "Your role cannot change classification rules (permission admin:classification). Nothing was changed.",
        field: null,
      };
    }
    if ((err.status === 422 || err.status === 409) && err.message) {
      return {
        kind: "error",
        text: err.message,
        field: concernsPattern(err.message) ? "pattern" : null,
      };
    }
  }
  return { kind: "error", text: `Not changed. ${actionErrorMessage(err)}`, field: null };
}

/** The same for a source write (override, review). */
export function sourceFailure(err: unknown): { kind: "forbidden" | "error"; text: string } {
  if (err instanceof ApiError && err.status === 403) {
    return {
      kind: "forbidden",
      text: "Your role cannot change a source's classification (permission log_source:write). Nothing was changed.",
    };
  }
  return { kind: "error", text: `Not changed. ${actionErrorMessage(err)}` };
}
