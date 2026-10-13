// IPS source administration: pure helpers.
//
// The generic field-mapping components and helpers speak in plain strings: a
// draft maps a field key to a name. An IPS reference also has a kind (a QRadar
// normalized field or a custom property), so the IPS draft carries the kinds
// next to the names and everything that must see a kind is computed here.
// Missing stays missing: a share that was not measured is a dash, never 0 %.

import {
  ApiError,
  type ActionValueMap,
  type DiscoveredValue,
  type FieldValidationOut,
  type IpsFieldReference,
  type IpsMapping,
  type IpsMappingSchemaOut,
  type IpsPresetKey,
  type IpsProfileHistoryEntryOut,
  type IpsProfileIn,
  type IpsProfileOut,
  type IpsReferenceKind,
  type IpsValidationFieldOut,
  type IpsValidationReportOut,
  type MappingSchemaOut,
  type WafProfileHistoryEntryOut,
  type WafValidationStatus,
} from "@/lib/api";
import {
  type DraftChanges,
  type FieldChange,
  type FieldStatusInfo,
  type MappingDraft,
  fieldStatusInfo,
  formatShare,
  reasonText,
  valueMapChanges,
} from "@/lib/fieldMapping";

export interface IpsDraft extends MappingDraft {
  /** The reference kind per field key. Meaningful only while the field is mapped. */
  kinds: Record<string, IpsReferenceKind>;
  /** Create mode only: the preset the form was filled from. Never trusted. */
  preset: IpsPresetKey | null;
}

/** The generic form's schema: the same fields, with each default reduced to its name. */
export function formSchema(schema: IpsMappingSchemaOut): MappingSchemaOut {
  return {
    domain: "IPS",
    property_name_pattern: schema.property_name_pattern,
    min_coverage_ratio: schema.min_coverage_ratio,
    fields: schema.fields.map((f) => ({ ...f, default: f.default?.name ?? null })),
    value_maps: schema.value_maps,
  };
}

function cloneActionMap(schema: IpsMappingSchemaOut, map: ActionValueMap | undefined): ActionValueMap {
  const out: ActionValueMap = {};
  for (const vm of schema.value_maps) {
    for (const outcome of vm.outcomes) out[outcome] = [...(map?.[outcome] ?? [])];
  }
  return out;
}

function firstKind(field: IpsMappingSchemaOut["fields"][number]): IpsReferenceKind {
  return field.default?.kind ?? field.kinds[0] ?? "NORMALIZED";
}

function fromReferences(
  schema: IpsMappingSchemaOut,
  refs: IpsMapping,
  actionMap: ActionValueMap | undefined,
  productLabel: string,
  preset: IpsPresetKey | null,
): IpsDraft {
  const mapping: Record<string, string> = {};
  const kinds: Record<string, IpsReferenceKind> = {};
  for (const f of schema.fields) {
    const ref = refs[f.key] ?? null;
    mapping[f.key] = ref?.name ?? "";
    kinds[f.key] = ref?.kind ?? firstKind(f);
  }
  return { productLabel, mapping, kinds, preset, actionMap: cloneActionMap(schema, actionMap) };
}

export function draftFromIpsProfile(schema: IpsMappingSchemaOut, profile: IpsProfileOut): IpsDraft {
  return fromReferences(
    schema,
    profile.mapping,
    profile.action_value_map,
    profile.product_label ?? "",
    profile.preset,
  );
}

/** A new profile's starting point: the schema defaults. */
export function draftFromDefaults(schema: IpsMappingSchemaOut): IpsDraft {
  const refs: IpsMapping = {};
  for (const f of schema.fields) refs[f.key] = f.default;
  return fromReferences(schema, refs, undefined, "", null);
}

/** Fills the form from a preset. The label the operator typed is kept. */
export function draftFromPreset(
  schema: IpsMappingSchemaOut,
  key: IpsPresetKey,
  productLabel: string,
): IpsDraft {
  const preset = schema.presets.find((p) => p.key === key);
  if (!preset) return { ...draftFromDefaults(schema), productLabel };
  return fromReferences(schema, preset.mapping, preset.action_value_map, productLabel, key);
}

/** The request body. Blank is sent as null; names and values are trimmed. */
export function draftToIpsInput(
  schema: IpsMappingSchemaOut,
  draft: IpsDraft,
  expectedVersion: number | null,
): IpsProfileIn {
  const mapping: IpsMapping = {};
  for (const f of schema.fields) {
    const name = draft.mapping[f.key]?.trim() ?? "";
    mapping[f.key] = name === "" ? null : { kind: draft.kinds[f.key] ?? firstKind(f), name };
  }
  const actionMap: ActionValueMap = { BLOCKED: [], ALLOWED: [] };
  for (const [outcome, values] of Object.entries(draft.actionMap)) {
    actionMap[outcome] = values.map((v) => v.trim()).filter((v) => v !== "");
  }
  return {
    expected_profile_version: expectedVersion,
    product_label: draft.productLabel.trim() || null,
    preset: draft.preset,
    mapping,
    action_value_map: actionMap,
  };
}

/** A reference in words: a normalized field by its name, a custom property labelled as one. */
export function referenceText(kind: IpsReferenceKind | string, name: string): string {
  return kind === "CUSTOM_PROPERTY" ? `${name} (custom property)` : name;
}

function norm(v: string | null | undefined): string | null {
  const t = (v ?? "").trim();
  return t === "" ? null : t;
}

/**
 * What a save would change. A field changes when its name changes or, for a
 * mapped field, its kind does: `sourceip` as a normalized field and as a
 * custom property of that name are different references.
 */
export function ipsDraftChanges(
  schema: IpsMappingSchemaOut,
  before: IpsDraft,
  after: IpsDraft,
): DraftChanges {
  const fields: FieldChange[] = [];
  for (const f of schema.fields) {
    const fromName = norm(before.mapping[f.key]);
    const toName = norm(after.mapping[f.key]);
    const fromKind = before.kinds[f.key] ?? firstKind(f);
    const toKind = after.kinds[f.key] ?? firstKind(f);
    if (fromName === toName && (fromName === null || fromKind === toKind)) continue;
    fields.push({
      key: f.key,
      label: f.label,
      from: fromName === null ? null : referenceText(fromKind, fromName),
      to: toName === null ? null : referenceText(toKind, toName),
    });
  }
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

// --- validation words --------------------------------------------------------

export function ipsFieldStatusInfo(
  status: string,
  ctx: Parameters<typeof fieldStatusInfo>[1],
): FieldStatusInfo {
  if (status === "FIELD_REJECTED") {
    return {
      label: "Field rejected",
      tone: ctx.usedByCollection ? "crit" : "warn",
      sentence: "QRadar does not accept this field as named.",
      silent: false,
    };
  }
  if (status === "UNPOPULATED") {
    return {
      ...fieldStatusInfo(status, ctx),
      sentence: "The field exists but no event in the window carried a usable value.",
    };
  }
  return fieldStatusInfo(status, ctx);
}

function capitalise(text: string): string {
  return text === "" ? text : `${text[0].toUpperCase()}${text.slice(1)}`;
}

/** A backend reason in the analyst's words; one it does not know is shown as recorded. */
export function ipsReasonText(
  reason: string,
  schema: IpsMappingSchemaOut,
  populatedRatio: (field: string) => number | null,
): string {
  const rejected = /^([A-Z][A-Z_]*): FIELD_REJECTED\b/.exec(reason);
  if (rejected) {
    const field = schema.fields.find((f) => f.key === rejected[1]);
    if (field) return `${field.label}: ${ipsFieldStatusInfo("FIELD_REJECTED", { populatedRatio: null, plausibleRatio: null, requiredCoverage: null, usedByCollection: field.used_by_collection }).sentence}`;
  }
  if (reason === "the bounded validation probe was truncated") {
    return "The bounded validation probe was truncated, so the result is inconclusive rather than valid.";
  }
  const asWaf = reasonText(reason, formSchema(schema), schema.min_coverage_ratio, populatedRatio);
  return asWaf === reason ? capitalise(reason) : asWaf;
}

/** One warning per field whose events carry a generic "Unknown …" signature name. */
export function genericNameNotes(report: IpsValidationReportOut): { key: string; text: string }[] {
  const notes: { key: string; text: string }[] = [];
  for (const f of report.fields) {
    if (f.generic_name_ratio != null && f.generic_name_ratio > 0) {
      notes.push({
        key: `generic-${f.field}`,
        text: `${formatShare(f.generic_name_ratio)} of events carry a generic “Unknown …” signature name. Those events cannot be told apart by signature. This is a warning; it does not stop collection.`,
      });
    }
  }
  // Warnings the report carries that this build has no sentence for stay visible.
  report.warnings
    .filter((w) => !/use a generic Unknown QID name$/.test(w))
    .forEach((w, i) => notes.push({ key: `warning-${i}`, text: capitalise(w) }));
  return notes;
}

/** The generic report shape the shared table reads. */
export function reportForTable(report: IpsValidationReportOut | null) {
  if (!report) return null;
  return {
    status: report.status as WafValidationStatus,
    reasons: report.reasons,
    total_events: report.total_events,
    window_start: report.window_start,
    window_end: report.window_end,
    profile_version: report.profile_version,
    fields: report.fields.map(
      (f: IpsValidationFieldOut): FieldValidationOut => ({
        field: f.field,
        property_name: f.reference?.name ?? null,
        status: f.status,
        used_by_collection: f.used_by_collection,
        populated_events: f.populated_events,
        populated_ratio: f.populated_ratio,
        distinct_values: null,
        plausible_ratio: null,
        sampled_events: null,
        values_truncated: f.values_truncated,
        discovered_values: f.discovered_values as DiscoveredValue[],
        detail: f.detail,
      }),
    ),
  };
}

export function referenceKindOf(
  report: IpsValidationReportOut | null,
  field: string,
): IpsReferenceKind | null {
  return report?.fields.find((f) => f.field === field)?.reference?.kind ?? null;
}

// --- history -----------------------------------------------------------------

const HISTORY_ACTIONS: Record<string, string> = {
  "ips.profile.created": "Created",
  "ips.profile.mapping_changed": "Mapping changed",
  "ips.profile.relabelled": "Label changed",
  "ips.profile.validation_requested": "Validation requested",
  "ips.profile.validated": "Validated",
  "ips.profile.enabled": "Collection enabled",
  "ips.profile.disabled": "Collection disabled",
};

export function ipsHistoryActionLabel(action: string): string {
  return HISTORY_ACTIONS[action] ?? action;
}

function flatten(map: Record<string, unknown> | null): Record<string, string | null> | null {
  if (map === null) return null;
  const out: Record<string, string | null> = {};
  for (const [key, value] of Object.entries(map)) {
    if (value && typeof value === "object") {
      const ref = value as Partial<IpsFieldReference>;
      out[key] = typeof ref.name === "string" ? referenceText(ref.kind ?? "", ref.name) : null;
    } else {
      out[key] = null;
    }
  }
  return out;
}

/** History entries in the shape the shared table reads. */
export function historyForTable(entries: IpsProfileHistoryEntryOut[]): WafProfileHistoryEntryOut[] {
  return entries.map((e) => ({
    at: e.at,
    actor: e.actor ?? "",
    action: e.action,
    profile_version: e.profile_version,
    mapping_before: flatten(e.mapping_before),
    mapping_after: flatten(e.mapping_after),
    outcome: e.outcome,
    reasons: e.reasons,
  }));
}

/** The sentence for an outcome that is more than "success" or "failure". */
export function ipsOutcomeNote(entry: WafProfileHistoryEntryOut): string | null {
  if (entry.action === "ips.profile.validated" && entry.outcome === "SOURCE_BUSY") {
    return "Validation could not take the source lock, so it did not run. The previous result is kept.";
  }
  if (entry.action === "ips.profile.validated" && entry.outcome === "FAILURE") {
    return "Validation failed to complete. The previous result is kept.";
  }
  return null;
}

/**
 * Whether the newest validation event of the current version ended without
 * running because the source was busy. A later request, or a result, replaces
 * it: only the newest decides.
 */
export function lastValidationWasBusy(
  entries: IpsProfileHistoryEntryOut[] | null,
  version: number,
): boolean {
  if (!entries) return false;
  const newest = entries.find(
    (e) => e.action === "ips.profile.validated" || e.action === "ips.profile.validation_requested",
  );
  return (
    newest !== undefined &&
    newest.action === "ips.profile.validated" &&
    newest.outcome === "SOURCE_BUSY" &&
    newest.profile_version === version
  );
}

/** Why a 403 happened, for this domain. */
export const IPS_PERMISSION = "admin:ips";

export function isForbidden(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403;
}
