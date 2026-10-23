// Field-mapping administration: pure helpers.
//
// Everything here is a function of the contract in docs/FIELD-MAPPING.md
// (§FM.3, §FM.4, §FM.6, §FM.8) and knows nothing about WAF. The schema is an
// argument, so the IPS screens can reuse these helpers with their own schema.
//
// One rule runs through the file: a measurement that is missing is shown as
// missing. A null share is an em dash, never "0 %"; a validation result that
// "says nothing about the field" is never worded as a failure of the field.

import {
  ApiError,
  type ActionValueMap,
  type DiscoveredValue,
  type FieldMapping,
  type MappingSchemaOut,
  type PropertyCatalogOut,
  type WafProfileHistoryEntryOut,
  type WafProfileIn,
  type WafValidationStatus,
} from "@/lib/api";
import { formatShare } from "@/lib/format";

export const MISSING = "—";

export type Tone = "ok" | "warn" | "crit" | "info" | "neutral";

// --- numbers ----------------------------------------------------------------

export { formatCount, formatShare } from "@/lib/format";

/** `HH:MM UTC`, for "since" lines where the date is not the point. */
export function formatClock(value: string | null | undefined): string {
  if (!value) return MISSING;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return MISSING;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// --- the editable draft -----------------------------------------------------

/** What the form edits. An empty string is "not mapped". */
export interface MappingDraft {
  productLabel: string;
  mapping: Record<string, string>;
  actionMap: ActionValueMap;
}

interface DraftSource {
  product_label?: string | null;
  mapping: FieldMapping;
  action_value_map: ActionValueMap;
}

function cloneActionMap(schema: MappingSchemaOut, map: ActionValueMap | undefined): ActionValueMap {
  const out: ActionValueMap = {};
  for (const vm of schema.value_maps) {
    for (const outcome of vm.outcomes) out[outcome] = [...(map?.[outcome] ?? [])];
  }
  return out;
}

export function draftFromProfile(schema: MappingSchemaOut, source: DraftSource): MappingDraft {
  const mapping: Record<string, string> = {};
  for (const f of schema.fields) mapping[f.key] = source.mapping[f.key] ?? "";
  return {
    productLabel: source.product_label ?? "",
    mapping,
    actionMap: cloneActionMap(schema, source.action_value_map),
  };
}

/** A new profile's starting point when no validated profile can seed it. */
export function draftFromDefaults(schema: MappingSchemaOut): MappingDraft {
  const mapping: Record<string, string> = {};
  for (const f of schema.fields) mapping[f.key] = f.default ?? "";
  return { productLabel: "", mapping, actionMap: cloneActionMap(schema, undefined) };
}

/** The request body. Blank is sent as null; names and values are trimmed. */
export function draftToInput(
  schema: MappingSchemaOut,
  draft: MappingDraft,
  expectedVersion: number | null,
): WafProfileIn {
  const mapping: FieldMapping = {};
  for (const f of schema.fields) mapping[f.key] = draft.mapping[f.key]?.trim() || null;
  const actionMap: ActionValueMap = {};
  for (const [outcome, values] of Object.entries(draft.actionMap)) {
    actionMap[outcome] = values.map((v) => v.trim()).filter((v) => v !== "");
  }
  return {
    expected_profile_version: expectedVersion,
    product_label: draft.productLabel.trim() || null,
    mapping,
    action_value_map: actionMap,
  };
}

// --- validating a draft in the browser (a convenience; the server decides) ---

/**
 * Why a property name cannot be sent, or null. The pattern is the server's,
 * served in the schema; if this browser cannot compile it, no client check is
 * made and the server's answer decides.
 */
export function checkPropertyName(name: string, pattern: string): string | null {
  const value = name.trim();
  if (value === "") return null;
  let re: RegExp;
  try {
    re = new RegExp(pattern);
  } catch {
    return null;
  }
  return re.test(value)
    ? null
    : `Not an allowed property name. Names match ${pattern} and are never sent to QRadar otherwise.`;
}

/** Field key to the message shown beside it. `@values` is the action value map. */
export function draftErrors(schema: MappingSchemaOut, draft: MappingDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of schema.fields) {
    const value = (draft.mapping[f.key] ?? "").trim();
    if (value === "") {
      if (f.required) errors[f.key] = "Required: collection cannot run without it.";
      continue;
    }
    const bad = checkPropertyName(value, schema.property_name_pattern);
    if (bad) errors[f.key] = bad;
  }
  for (const vm of schema.value_maps) {
    const mapped = vm.outcomes.some((o) => (draft.actionMap[o] ?? []).length > 0);
    if (mapped && (draft.mapping[vm.field] ?? "").trim() === "") {
      errors["@values"] =
        "Values are assigned but no property is mapped for this field. Map the property first.";
    }
    const seen = new Map<string, string>();
    for (const o of vm.outcomes) {
      for (const v of draft.actionMap[o] ?? []) {
        const prior = seen.get(v);
        if (prior && prior !== o) {
          errors["@values"] = `The value “${v}” is in both lists. Assign it to one.`;
        }
        seen.set(v, o);
      }
    }
  }
  return errors;
}

// --- what a save would change -----------------------------------------------

export interface FieldChange {
  key: string;
  label: string;
  from: string | null;
  to: string | null;
}

export interface ValueChange {
  outcome: string;
  added: string[];
  removed: string[];
}

function norm(v: string | null | undefined): string | null {
  const t = (v ?? "").trim();
  return t === "" ? null : t;
}

export function mappingChanges(
  schema: MappingSchemaOut,
  before: MappingDraft,
  after: MappingDraft,
): FieldChange[] {
  const out: FieldChange[] = [];
  for (const f of schema.fields) {
    const from = norm(before.mapping[f.key]);
    const to = norm(after.mapping[f.key]);
    if (from !== to) out.push({ key: f.key, label: f.label, from, to });
  }
  return out;
}

export function valueMapChanges(before: ActionValueMap, after: ActionValueMap): ValueChange[] {
  const out: ValueChange[] = [];
  const outcomes = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
  for (const outcome of outcomes) {
    const b = new Set((before[outcome] ?? []).map((v) => v.trim()).filter(Boolean));
    const a = new Set((after[outcome] ?? []).map((v) => v.trim()).filter(Boolean));
    const added = [...a].filter((v) => !b.has(v));
    const removed = [...b].filter((v) => !a.has(v));
    if (added.length || removed.length) out.push({ outcome, added, removed });
  }
  return out;
}

export interface DraftChanges {
  fields: FieldChange[];
  values: ValueChange[];
  /** A label-only change does not bump the version or request validation. */
  labelChanged: boolean;
  /** True when the save is a mapping change: version bump, validation reset. */
  mappingChanged: boolean;
  total: number;
}

export function draftChanges(
  schema: MappingSchemaOut,
  before: MappingDraft,
  after: MappingDraft,
): DraftChanges {
  const fields = mappingChanges(schema, before, after);
  const values = valueMapChanges(before.actionMap, after.actionMap);
  const labelChanged = norm(before.productLabel) !== norm(after.productLabel);
  const valueCount = values.reduce((n, v) => n + v.added.length + v.removed.length, 0);
  return {
    fields,
    values,
    labelChanged,
    mappingChanged: fields.length > 0 || values.length > 0,
    total: fields.length + valueCount + (labelChanged ? 1 : 0),
  };
}

// --- the property catalog ---------------------------------------------------

export interface CatalogNote {
  kind: "not-listed" | "wrong-type";
  text: string;
}

/**
 * The two non-blocking warnings beside a property input (§FM.8 item 2). The
 * catalog is a suggestion, never a gate: neither warning stops a save.
 * Nothing is said while the catalog is unavailable, because then the list says
 * nothing about the name.
 */
export function catalogNote(
  name: string,
  catalog: PropertyCatalogOut | null,
  typeName: string | null,
): CatalogNote | null {
  const value = name.trim();
  if (value === "" || catalog === null || catalog.status !== "AVAILABLE") return null;
  const hit = catalog.properties.find((p) => p.name === value);
  if (!hit) {
    return {
      kind: "not-listed",
      text: "Not in this console’s property list. Validation will check it.",
    };
  }
  if (hit.applies_to_source_type === false) {
    return {
      kind: "wrong-type",
      text: `This property has no parsing expression for this source’s log-source type${
        typeName ? ` (${typeName})` : ""
      }.`,
    };
  }
  return null;
}

// --- property search -------------------------------------------------------

/** Split on spaces, `_`, `-` and camelCase boundaries; lower-cased, no empties. */
export function searchTokens(text: string): string[] {
  return text
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .split(/[^A-Za-z0-9]+/)
    .filter((t) => t !== "")
    .map((t) => t.toLowerCase());
}

export interface PropertyMatch {
  /** Lower is better: the number of query tokens that start inside a word. */
  score: number;
  /** Half-open [start, end) character ranges of `name` to emphasise. */
  ranges: [number, number][];
}

/**
 * Whether every token of `query` occurs in `name`, ignoring case and separators.
 * Matching runs on the name with its separators removed, so `responsecode`,
 * `resp code` and `waf resp` all find `WAF_ResponseCode`. An empty query matches
 * everything with nothing to emphasise.
 */
export function matchProperty(name: string, query: string): PropertyMatch | null {
  const tokens = searchTokens(query);
  if (tokens.length === 0) return { score: 0, ranges: [] };

  const compact: string[] = [];
  const origin: number[] = []; // compact index -> index in `name`
  const wordStart = new Set<number>();
  let prevAlnum = false;
  let prevLower = false;
  for (let i = 0; i < name.length; i++) {
    const ch = name[i];
    const alnum = /[A-Za-z0-9]/.test(ch);
    if (alnum) {
      const upper = /[A-Z]/.test(ch);
      if (!prevAlnum || (upper && prevLower)) wordStart.add(compact.length);
      compact.push(ch.toLowerCase());
      origin.push(i);
      prevLower = /[a-z0-9]/.test(ch);
    }
    prevAlnum = alnum;
  }
  const flat = compact.join("");

  let score = 0;
  const spans: [number, number][] = [];
  for (const token of tokens) {
    const first = flat.indexOf(token);
    if (first < 0) return null;
    // Prefer an occurrence that starts a word, so `code` marks the word Code.
    let at = first;
    for (let i = first; i >= 0; i = flat.indexOf(token, i + 1)) {
      if (wordStart.has(i)) {
        at = i;
        break;
      }
    }
    if (!wordStart.has(at)) score += 1;
    spans.push([at, at + token.length]);
  }

  spans.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const [a, b] of spans) {
    const last = merged[merged.length - 1];
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else merged.push([a, b]);
  }
  // Back to positions in the original name; a run keeps any separator inside it.
  const ranges = merged.map(([a, b]): [number, number] => [origin[a], origin[b - 1] + 1]);
  return { score, ranges };
}

// --- validation words -------------------------------------------------------

export interface FieldStatusInfo {
  label: string;
  tone: Tone;
  sentence: string;
  /** The result says nothing about the field: it is neither a pass nor a fail. */
  silent: boolean;
}

export function fieldStatusInfo(
  status: string,
  ctx: {
    populatedRatio: number | null;
    plausibleRatio: number | null;
    requiredCoverage: number | null;
    usedByCollection: boolean;
    detail?: string | null;
  },
): FieldStatusInfo {
  const fail: Tone = ctx.usedByCollection ? "crit" : "warn";
  switch (status) {
    case "AVAILABLE":
      return { label: "Usable", tone: "ok", sentence: "Populated, and the values look right.", silent: false };
    case "NOT_MAPPED":
      return {
        label: "Not mapped",
        tone: ctx.usedByCollection ? "crit" : "neutral",
        sentence: "No property is mapped to this field.",
        silent: false,
      };
    case "PROPERTY_REJECTED":
      return {
        label: "Property rejected",
        tone: fail,
        sentence: "QRadar does not know a property with this name.",
        silent: false,
      };
    case "UNPOPULATED":
      return {
        label: "Not populated",
        tone: fail,
        sentence: "The property exists but no event in the window carried it.",
        silent: false,
      };
    case "INSUFFICIENT_COVERAGE": {
      const got = formatShare(ctx.populatedRatio);
      const need = ctx.requiredCoverage == null ? null : formatShare(ctx.requiredCoverage);
      const head = got === MISSING ? "Too few events carry it" : `Only ${got} of events carry it`;
      return {
        label: "Low coverage",
        tone: fail,
        sentence: need ? `${head}; collection needs ${need}.` : `${head}.`,
        silent: false,
      };
    }
    case "IMPLAUSIBLE": {
      const share = formatShare(ctx.plausibleRatio);
      return {
        label: "Values look wrong",
        tone: fail,
        sentence:
          share === MISSING
            ? "The property is populated but its values do not look like this field."
            : `The property is populated but only ${share} of sampled values look like this field.`,
        silent: false,
      };
    }
    case "NO_EVENTS":
      return {
        label: "No verdict",
        tone: "neutral",
        sentence: "The source sent no events in the window. This says nothing about the field.",
        silent: true,
      };
    case "QUERY_FAILED":
      return {
        label: "No verdict",
        tone: "neutral",
        sentence: "The check could not run. This says nothing about the field.",
        silent: true,
      };
    default:
      return {
        label: status,
        tone: "neutral",
        sentence: "This build does not recognise this status; it is shown as recorded.",
        silent: false,
      };
  }
}

// --- one profile's state, in words ------------------------------------------

/** What the state words read: a profile, or a row of the status list. */
export interface ProfileStateInput {
  enabled: boolean;
  profile_version: number;
  validation_status: WafValidationStatus;
  validated_profile_version: number | null;
  collection_eligible: boolean;
  ineligible_reason: string | null;
  validation_pending?: boolean;
  /** Per-field outcomes, for the reason after "Invalid:". */
  fields?: { field: string; property_name: string | null; status: string }[];
}

export type ValidationKind =
  | "valid"
  | "invalid"
  | "inconclusive"
  | "unvalidated"
  | "pending";

export interface ValidationState {
  kind: ValidationKind;
  label: string;
  tone: Tone;
}

const FAILING_ORDER = [
  "PROPERTY_REJECTED",
  "NOT_MAPPED",
  "UNPOPULATED",
  "INSUFFICIENT_COVERAGE",
  "IMPLAUSIBLE",
];

/** A short reason for an INVALID or INCONCLUSIVE result, from the field outcomes. */
export function validationReason(
  fields: ProfileStateInput["fields"],
  schema: MappingSchemaOut | null,
  inconclusive: boolean,
): string | null {
  if (!fields || fields.length === 0) return null;
  const label = (key: string) => schema?.fields.find((f) => f.key === key)?.label ?? key;
  if (inconclusive) {
    if (fields.some((f) => f.status === "NO_EVENTS")) return "no events in the window";
    if (fields.some((f) => f.status === "QUERY_FAILED")) return "a check could not run";
    return null;
  }
  for (const status of FAILING_ORDER) {
    const hit = fields.find((f) => f.status === status);
    if (!hit) continue;
    const who = `${label(hit.field)} property`;
    switch (status) {
      case "PROPERTY_REJECTED":
        return `${who} does not exist`;
      case "NOT_MAPPED":
        return `${label(hit.field)} is not mapped`;
      case "UNPOPULATED":
        return `${who} carries no values`;
      case "INSUFFICIENT_COVERAGE":
        return `${who} is rarely populated`;
      default:
        return `${who} values look wrong`;
    }
  }
  return null;
}

/**
 * The validation column's text. Fail closed: a VALID result counts only for
 * the version it checked, so "Valid" is claimed only when that version is the
 * current one.
 */
export function validationState(
  p: ProfileStateInput,
  schema: MappingSchemaOut | null = null,
): ValidationState {
  const v = p.profile_version;
  if (p.validation_pending) return { kind: "pending", label: `Validating v${v}…`, tone: "info" };
  const current = p.validated_profile_version === v;
  if (p.validation_status === "VALID" && current) {
    return { kind: "valid", label: `Valid (v${v})`, tone: "ok" };
  }
  if (p.validation_status === "INVALID" && current) {
    const why = validationReason(p.fields, schema, false);
    return { kind: "invalid", label: why ? `Invalid: ${why}` : "Invalid", tone: "crit" };
  }
  if (p.validation_status === "INCONCLUSIVE" && current) {
    const why = validationReason(p.fields, schema, true);
    return {
      kind: "inconclusive",
      label: why ? `Inconclusive: ${why}` : "Inconclusive",
      tone: "warn",
    };
  }
  return { kind: "unvalidated", label: `Version ${v} not validated`, tone: "warn" };
}

export function collectionState(p: ProfileStateInput): { collecting: boolean; label: string } {
  if (p.collection_eligible && p.enabled) return { collecting: true, label: "Collecting" };
  return {
    collecting: false,
    label: p.ineligible_reason ? `Paused — ${p.ineligible_reason}` : "Paused",
  };
}

/** Lower sorts first. Pending work ranks below findings nobody is acting on. */
export function attentionRank(p: ProfileStateInput): number {
  const state = validationState(p);
  if (state.kind === "pending") return 3;
  if (state.kind === "invalid") return 0;
  if (state.kind === "inconclusive") return 1;
  if (state.kind === "unvalidated") return 2;
  if (!p.collection_eligible || !p.enabled) return 4;
  return 5;
}

/** Needs attention first, then name. Does not mutate its input. */
export function sortByAttention<T extends ProfileStateInput & { log_source_name: string }>(
  rows: T[],
): T[] {
  return [...rows].sort(
    (a, b) =>
      attentionRank(a) - attentionRank(b) ||
      a.log_source_name.localeCompare(b.log_source_name, "en", { sensitivity: "base" }),
  );
}

/** Why Enable is unavailable, in words; null when it may be pressed. */
export function enableBlockReason(p: ProfileStateInput): string | null {
  const state = validationState(p);
  switch (state.kind) {
    case "valid":
      return null;
    case "pending":
      return `Version ${p.profile_version} is still being validated. Enable becomes available when it validates as valid.`;
    case "invalid":
      return `Version ${p.profile_version} validated as invalid. Fix the mapping and validate again.`;
    case "inconclusive":
      return `Version ${p.profile_version} validated as inconclusive. Validate again when the source has events.`;
    default:
      return `Version ${p.profile_version} has not been validated. Validate it first.`;
  }
}

// --- concurrency ------------------------------------------------------------

export interface ConflictInfo {
  version: number | null;
  by: string | null;
  /** The server's own sentence; safe to show. */
  message: string | null;
}

/**
 * What a 409 names. Every 409 carries a structured detail
 * `{message, current_version, updated_by, pending_since}` (docs/FIELD-MAPPING.md
 * §FM.4); a conflict found by polling has only the version and author.
 */
export function conflictInfo(err: unknown): ConflictInfo {
  const none = { version: null, by: null, message: null };
  if (!(err instanceof ApiError)) return none;
  const d = err.detail;
  if (!d || typeof d !== "object") return none;
  const o = d as Record<string, unknown>;
  return {
    version: typeof o.current_version === "number" ? o.current_version : null,
    by: typeof o.updated_by === "string" ? o.updated_by : null,
    message: typeof o.message === "string" ? o.message : null,
  };
}

export function conflictMessage(info: ConflictInfo): string {
  const keep = "Your edits are kept below; reload to see theirs.";
  if (info.message) return `${info.message} ${keep}`;
  const to = info.version == null ? "a newer version" : `version ${info.version}`;
  const by = info.by ? ` (by ${info.by})` : "";
  return `This mapping changed to ${to} while you were editing${by}. ${keep}`;
}

/** The server's message for an error: `detail.message`, else the plain detail. */
export function serverMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const d = err.detail;
    if (d && typeof d === "object" && typeof (d as { message?: unknown }).message === "string") {
      return (d as { message: string }).message;
    }
    return err.message;
  }
  return "";
}

const REASON_RE = /^([A-Z][A-Z_]*): ([A-Z][A-Z_]*) \((.*)\)$/s;
const KNOWN_FIELD_STATUS = new Set([
  "AVAILABLE", "NOT_MAPPED", "PROPERTY_REJECTED", "UNPOPULATED",
  "INSUFFICIENT_COVERAGE", "IMPLAUSIBLE", "NO_EVENTS", "QUERY_FAILED",
]);

/**
 * A backend validation reason in the analyst's words. Reasons have the form
 * `<FIELD>: <STATUS> (<detail>)`; one that names a schema field and a known
 * status reads "<label>: <the sentence the result table uses>". Anything else
 * is shown verbatim.
 */
export function reasonText(
  reason: string,
  schema: MappingSchemaOut,
  requiredCoverage: number | null,
  populatedRatio: (field: string) => number | null = () => null,
): string {
  const m = REASON_RE.exec(reason);
  if (!m) return reason;
  const [, key, st] = m;
  const field = schema.fields.find((f) => f.key === key);
  if (!field || !KNOWN_FIELD_STATUS.has(st)) return reason;
  const info = fieldStatusInfo(st, {
    populatedRatio: populatedRatio(key),
    plausibleRatio: null,
    requiredCoverage,
    usedByCollection: field.used_by_collection,
  });
  return `${field.label}: ${info.sentence}`;
}

// --- the action value map ---------------------------------------------------

export type Assignment = string | null | "BOTH";

/** Which outcome a value is assigned to; null = not mapped, "BOTH" = invalid. */
export function assignmentOf(map: ActionValueMap, outcomes: string[], value: string): Assignment {
  const hits = outcomes.filter((o) => (map[o] ?? []).includes(value));
  if (hits.length === 0) return null;
  return hits.length === 1 ? hits[0] : "BOTH";
}

export function assignValue(
  map: ActionValueMap,
  outcomes: string[],
  value: string,
  to: string | null,
): ActionValueMap {
  const next: ActionValueMap = {};
  for (const o of outcomes) {
    next[o] = (map[o] ?? []).filter((v) => v !== value);
    if (o === to) next[o].push(value);
  }
  return next;
}

export interface ValueRow {
  value: string;
  /** Null when the value was not in the last validation's list. */
  events: number | null;
  seen: boolean;
}

/** Discovered values (by the report's order), then mapped values not seen. */
export function valueRows(
  discovered: DiscoveredValue[],
  map: ActionValueMap,
  outcomes: string[],
): ValueRow[] {
  const rows: ValueRow[] = discovered.map((d) => ({ value: d.value, events: d.events, seen: true }));
  const known = new Set(discovered.map((d) => d.value));
  for (const o of outcomes) {
    for (const value of map[o] ?? []) {
      if (!known.has(value)) {
        known.add(value);
        rows.push({ value, events: null, seen: false });
      }
    }
  }
  return rows;
}

// --- history ----------------------------------------------------------------

const HISTORY_ACTIONS: Record<string, string> = {
  "waf.profile.created": "Created",
  "waf.profile.mapping_changed": "Mapping changed",
  "waf.profile.relabelled": "Label changed",
  "waf.profile.validation_requested": "Validation requested",
  "waf.profile.validated": "Validated",
  "waf.profile.enabled": "Collection enabled",
  "waf.profile.disabled": "Collection disabled",
};

export function historyActionLabel(action: string): string {
  return HISTORY_ACTIONS[action] ?? action;
}

/** The mapping differences an entry recorded, for its "what changed" cell. */
export function historyChanges(
  schema: MappingSchemaOut,
  entry: WafProfileHistoryEntryOut,
): FieldChange[] {
  const keys = schema.fields.map((f) => f.key);
  const before = entry.mapping_before ?? {};
  const after = entry.mapping_after ?? {};
  const out: FieldChange[] = [];
  for (const key of keys) {
    const from = norm(before[key]);
    const to = norm(after[key]);
    if (from !== to) {
      out.push({ key, label: schema.fields.find((f) => f.key === key)?.label ?? key, from, to });
    }
  }
  return out;
}

/**
 * The field a 422 message is about, when it names exactly one of the property
 * names in the draft. The contract serves one message, not a field key, so
 * anything else is shown at the form.
 */
export function fieldForMessage(
  schema: MappingSchemaOut,
  draft: MappingDraft,
  message: string,
): string | null {
  const hits = schema.fields.filter((f) => {
    const name = (draft.mapping[f.key] ?? "").trim();
    return name !== "" && message.includes(name);
  });
  return hits.length === 1 ? hits[0].key : null;
}

/** A draft with every field unmapped: the baseline a new profile is diffed against. */
export function emptyDraft(schema: MappingSchemaOut): MappingDraft {
  const mapping: Record<string, string> = {};
  for (const f of schema.fields) mapping[f.key] = "";
  return { productLabel: "", mapping, actionMap: cloneActionMap(schema, undefined) };
}

interface PrefillCandidate {
  log_source_id: string;
  instance_id: string;
  validation_status: WafValidationStatus;
  validated_profile_version: number | null;
  profile_version: number;
  validated_at: string | null;
}

/**
 * The instance a new profile belongs to: the one the log source names, else the
 * only instance that has any profile. With several and no hint it is unknown,
 * and the prefill falls back to the schema defaults rather than guessing.
 */
export function resolveInstanceId(
  named: string | null | undefined,
  sources: { instance_id: string }[],
): string | null {
  if (named) return named;
  const ids = new Set(sources.map((s) => s.instance_id));
  return ids.size === 1 ? [...ids][0] : null;
}

/**
 * §FM.7: the most recently validated VALID profile on the same instance. VALID
 * counts only for the version it checked (fail closed), and the source being
 * configured never seeds itself.
 */
export function pickPrefillSource<T extends PrefillCandidate>(
  sources: T[],
  instanceId: string | null,
  selfLogSourceId: string,
): T | null {
  if (instanceId === null) return null;
  const valid = sources.filter(
    (s) =>
      s.instance_id === instanceId &&
      s.log_source_id !== selfLogSourceId &&
      s.validation_status === "VALID" &&
      s.validated_profile_version === s.profile_version &&
      s.validated_at !== null,
  );
  valid.sort((a, b) => Date.parse(b.validated_at ?? "") - Date.parse(a.validated_at ?? ""));
  return valid[0] ?? null;
}
