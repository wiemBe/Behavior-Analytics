import {
  ApiError,
  type CallerInfo,
  type PropertyWatch,
  type PropertyWatchCatalog,
  type PropertyWatchCreate,
  type PropertyWatchUpdate,
  type PwAnomalyTypeName,
  type PwPropertyOptions,
  type PwPropertyRef,
  type PwScopeKind,
  type PwScopeOption,
} from "@/lib/api";

export const PROPERTY_WATCH_WRITE = "property_watch:write";

export type PropertyWatchAccess = "writer" | "reader" | "unknown";

export function propertyWatchAccessFrom(caller: CallerInfo): PropertyWatchAccess {
  return caller.effective_permissions.includes(PROPERTY_WATCH_WRITE) ||
    caller.permissions.includes(PROPERTY_WATCH_WRITE) ||
    caller.permissions.includes("admin:*")
    ? "writer"
    : "reader";
}

export const SCOPE_KIND_LABELS: Record<PwScopeKind, string> = {
  LOG_SOURCE: "Log source",
  LOG_SOURCE_TYPE: "Log source type",
  LOG_SOURCE_GROUP: "Log source group",
};

export const DETECTORS: Record<
  PwAnomalyTypeName,
  { label: string; claim: string; learning: string }
> = {
  PW_VALUE_SPIKE: {
    label: "Value spike",
    claim: "A value rose above its seasonal expected range.",
    learning: "Needs a week of reliable baselines.",
  },
  PW_VALUE_DROP: {
    label: "Value drop",
    claim: "A value's exact count fell below its seasonal expected range.",
    learning: "Needs a week of reliable baselines and adequate parsing coverage.",
  },
  PW_VALUE_DISAPPEARED: {
    label: "Value disappeared",
    claim: "A normally present value had a measured zero for several buckets.",
    learning: "Needs a week of reliable baselines and exact zeros.",
  },
  PW_NEW_VALUE: {
    label: "New value",
    claim: "A material value absent from verified history appeared.",
    learning: "Needs 7 days of complete novelty history.",
  },
  PW_NEW_COMBINATION: {
    label: "New combination",
    claim: "A pair of values, each seen before, appeared together for the first time.",
    learning: "Needs two properties and 7 days of complete component history.",
  },
};

export interface WatchDraft {
  name: string;
  description: string;
  scopeKind: PwScopeKind;
  scopeId: string;
  scopeLabel: string;
  scopeCount: number | null;
  properties: PwPropertyRef[];
  pins: string[][];
  detectors: PwAnomalyTypeName[];
  severity: string;
  /** Zero means learn forward only. Untouched lets create retain the server's default. */
  historyDays: number;
  historyTouched: boolean;
}

export function emptyWatchDraft(catalog: PropertyWatchCatalog): WatchDraft {
  return {
    name: "",
    description: "",
    scopeKind: "LOG_SOURCE",
    scopeId: "",
    scopeLabel: "",
    scopeCount: null,
    properties: [{ kind: "NORMALIZED", name: catalog.normalized_fields[0] ?? "username" }],
    pins: [],
    detectors: catalog.detectors.filter(
      (d): d is PwAnomalyTypeName => d in DETECTORS && d !== "PW_NEW_COMBINATION",
    ),
    severity: "MEDIUM",
    historyDays: catalog.history.enabled ? catalog.history.days_default : 0,
    historyTouched: false,
  };
}

export function draftFromWatch(watch: PropertyWatch): WatchDraft {
  const scopeId =
    watch.scope_kind === "LOG_SOURCE"
      ? (watch.log_source_id ?? "")
      : String(
          watch.scope_kind === "LOG_SOURCE_TYPE"
            ? (watch.scope_type_id ?? "")
            : (watch.scope_group_id ?? ""),
        );
  return {
    name: watch.name,
    description: watch.description ?? "",
    scopeKind: watch.scope_kind,
    scopeId,
    scopeLabel: watch.scope.name ?? scopeId,
    scopeCount: watch.scope.member_count,
    properties: watch.properties.map((property) => ({ ...property })),
    pins: watch.pinned_values.map((pin) => [...pin]),
    detectors: [...watch.detectors],
    severity: watch.severity,
    historyDays: watch.history.days_requested ?? 0,
    historyTouched: false,
  };
}

export function scopeOptionTooLarge(option: PwScopeOption): boolean {
  return option.scope_state === "SCOPE_TOO_LARGE";
}

export function choosePropertyKind(name: string, normalized: string[]): PwPropertyRef["kind"] {
  return normalized.includes(name) ? "NORMALIZED" : "CUSTOM_PROPERTY";
}

const PROPERTY_NAME = /^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$/;

export function propertyReason(property: PwPropertyRef): string | null {
  if (property.kind === "NORMALIZED") {
    return property.name === "username"
      ? null
      : "The normalized field is not in the property-watch allowlist.";
  }
  if (!PROPERTY_NAME.test(property.name)) {
    return "Use 1–64 letters, digits, underscores, spaces or hyphens; begin with a letter, digit or underscore.";
  }
  return null;
}

export function pinReason(value: string, maxLength: number): string | null {
  if (value.length === 0) return "Enter a value or remove this row.";
  if (value.length > maxLength) return `A pinned value is at most ${maxLength} characters.`;
  if (
    value !== value.trim() ||
    /['"\\]/.test(value) ||
    /[\u0000-\u001f\u007f-\u009f]/.test(value)
  ) {
    return "Quotes, backslashes, control characters and leading or trailing whitespace cannot be pinned.";
  }
  return null;
}

export function draftReasons(draft: WatchDraft, catalog: PropertyWatchCatalog): string[] {
  const reasons: string[] = [];
  if (draft.name.trim() === "") reasons.push("Name is required.");
  else if (draft.name.trim().length > 80) reasons.push("Name must be 80 characters or fewer.");
  if (draft.description.trim().length > 2000) {
    reasons.push("Description must be 2,000 characters or fewer.");
  }
  if (!draft.scopeId) reasons.push(`Choose a ${SCOPE_KIND_LABELS[draft.scopeKind].toLowerCase()}.`);
  if (draft.properties.length < 1 || draft.properties.length > catalog.max_properties) {
    reasons.push(`Choose one or two properties; the configured limit is ${catalog.max_properties}.`);
  }
  for (const property of draft.properties) {
    const reason = propertyReason(property);
    if (reason) reasons.push(reason);
  }
  if (new Set(draft.properties.map((p) => `${p.kind}:${p.name}`)).size !== draft.properties.length) {
    reasons.push("A watch cannot name the same property twice.");
  }
  if (draft.pins.length > catalog.max_pinned_values) {
    reasons.push(`A watch pins at most ${catalog.max_pinned_values} values.`);
  }
  draft.pins.forEach((pin, pinIndex) => {
    if (pin.length !== draft.properties.length) {
      reasons.push(`Pinned value ${pinIndex + 1} needs one entry per property.`);
      return;
    }
    pin.forEach((value) => {
      const reason = pinReason(value, catalog.value_max_length);
      if (reason) reasons.push(reason);
    });
  });
  if (new Set(draft.pins.map((pin) => JSON.stringify(pin))).size !== draft.pins.length) {
    reasons.push("A value is pinned twice.");
  }
  if (draft.detectors.length === 0) reasons.push("Choose at least one detector.");
  if (draft.properties.length !== 2 && draft.detectors.includes("PW_NEW_COMBINATION")) {
    reasons.push("New-combination detection needs exactly two properties.");
  }
  return [...new Set(reasons)];
}

export function createWatchBody(draft: WatchDraft): PropertyWatchCreate {
  const numeric = draft.scopeId === "" ? null : Number(draft.scopeId);
  const body: PropertyWatchCreate = {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    scope_kind: draft.scopeKind,
    log_source_id: draft.scopeKind === "LOG_SOURCE" ? draft.scopeId || null : null,
    scope_type_id: draft.scopeKind === "LOG_SOURCE_TYPE" ? numeric : null,
    scope_group_id: draft.scopeKind === "LOG_SOURCE_GROUP" ? numeric : null,
    properties: draft.properties,
    pinned_values: draft.pins,
    detectors: draft.detectors,
    severity: draft.severity,
  };
  if (draft.historyTouched) body.history_days = draft.historyDays;
  return body;
}

export function updateWatchBody(draft: WatchDraft, watch: PropertyWatch): PropertyWatchUpdate {
  return { expected_updated_at: watch.updated_at, ...createWatchBody(draft), enabled: false };
}

export function watchSummary(draft: WatchDraft): string {
  const properties = draft.properties.map((p) => p.name || "an unnamed property").join(" × ");
  const scope = draft.scopeLabel || `an unselected ${SCOPE_KIND_LABELS[draft.scopeKind].toLowerCase()}`;
  const count = draft.scopeCount === null ? "" : ` (${draft.scopeCount.toLocaleString("en-US")} sources)`;
  const detection = draft.detectors.map((d) => DETECTORS[d].label.toLowerCase()).join(", ") || "no";
  const history = draft.historyDays > 0 ? ` reads ${draft.historyDays} days of history first;` : "";
  return `Tracks ${properties} on ${scope}${count}, counted on collected 5-minute buckets;${history} ${draft.pins.length} ${draft.pins.length === 1 ? "value" : "values"} always counted; ${detection} detection on.`;
}

export function propertyApplicability(
  property: PwPropertyRef,
  options: PwPropertyOptions | null,
): string {
  if (property.kind === "NORMALIZED") return "QRadar normalized field";
  const option = options?.properties.find((row) => row.name === property.name);
  if (!option) return "Not in the catalog; the preview will show whether it parses.";
  return `Defined on ${option.defined_on_type_ids.length} of ${options?.scope_type_ids.length ?? 0} types in this scope.`;
}

type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue | null =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as RecordValue)
    : null;

export type WatchWriteRefusal =
  | { kind: "reasons"; reasons: string[] }
  | { kind: "version"; currentVersion: string; updatedBy: string | null }
  | { kind: "dependencies"; message: string; rules: Array<{ id: string; name: string }> }
  | { kind: "rate"; detail: string }
  | { kind: "conflict"; detail: string }
  | { kind: "error"; detail: string };

export function classifyWatchError(error: unknown): WatchWriteRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: "error", detail: "The property watch could not be saved. Try again." };
  }
  const detail = record(error.detail);
  if (error.status === 422 && Array.isArray(detail?.reasons)) {
    return {
      kind: "reasons",
      reasons: detail.reasons.filter((reason): reason is string => typeof reason === "string"),
    };
  }
  if (error.status === 409 && typeof detail?.current_version === "string") {
    return {
      kind: "version",
      currentVersion: detail.current_version,
      updatedBy: typeof detail.updated_by === "string" ? detail.updated_by : null,
    };
  }
  if (error.status === 409 && Array.isArray(detail?.rules)) {
    return {
      kind: "dependencies",
      message: typeof detail.message === "string" ? detail.message : "A custom rule pins this watch.",
      rules: detail.rules.flatMap((item) => {
        const rule = record(item);
        return typeof rule?.id === "string" && typeof rule.name === "string"
          ? [{ id: rule.id, name: rule.name }]
          : [];
      }),
    };
  }
  const text = typeof error.detail === "string" ? error.detail : error.message;
  if (error.status === 429) return { kind: "rate", detail: text };
  if (error.status === 409) return { kind: "conflict", detail: text };
  return { kind: "error", detail: "The property watch could not be saved. Check the backend, then try again." };
}
