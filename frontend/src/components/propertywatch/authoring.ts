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
import { renderBackendMessage } from "@/i18n/backendMessage";
import { wordOrValue } from "@/lib/behaviorI18n";
import { formatCount } from "@/lib/format";
import { refusalSentences } from "@/lib/refusal";

import { ENGLISH_PW, type PwI18n } from "./pwText";

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

export function authoringScopeKindLabel(
  kind: PwScopeKind,
  i18n: PwI18n = ENGLISH_PW,
): string {
  if (!Object.hasOwn(SCOPE_KIND_LABELS, kind)) return kind;
  return wordOrValue(i18n, "propertyWatch.scopeKind", kind);
}

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

export function detectorCopy(
  detector: PwAnomalyTypeName,
  i18n: PwI18n = ENGLISH_PW,
): { label: string; claim: string; learning: string } {
  if (!Object.hasOwn(DETECTORS, detector)) {
    return { label: detector, claim: detector, learning: detector };
  }
  return {
    label: wordOrValue(i18n, "enums.propertyWatchAnomalyType", detector),
    claim: i18n.t(`propertyWatch.detector.${detector}.claim`),
    learning: i18n.t(`propertyWatch.detector.${detector}.learning`),
  };
}

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
      (d): d is PwAnomalyTypeName => Object.hasOwn(DETECTORS, d) && d !== "PW_NEW_COMBINATION",
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

export function propertyReason(
  property: PwPropertyRef,
  i18n: PwI18n = ENGLISH_PW,
): string | null {
  if (property.kind === "NORMALIZED") {
    return property.name === "username"
      ? null
      : i18n.t("propertyWatch.validation.normalizedNotAllowed");
  }
  if (!PROPERTY_NAME.test(property.name)) {
    return i18n.t("propertyWatch.validation.propertyPattern");
  }
  return null;
}

export function pinReason(
  value: string,
  maxLength: number,
  i18n: PwI18n = ENGLISH_PW,
): string | null {
  if (value.length === 0) return i18n.t("propertyWatch.validation.pinRequired");
  if (value.length > maxLength) {
    return i18n.t("propertyWatch.validation.pinLength", { max: formatCount(maxLength, i18n.locale) });
  }
  if (
    value !== value.trim() ||
    /['"\\]/.test(value) ||
    /[\u0000-\u001f\u007f-\u009f]/.test(value)
  ) {
    return i18n.t("propertyWatch.validation.pinCharacters");
  }
  return null;
}

export function draftReasons(
  draft: WatchDraft,
  catalog: PropertyWatchCatalog,
  i18n: PwI18n = ENGLISH_PW,
): string[] {
  const reasons: string[] = [];
  if (draft.name.trim() === "") reasons.push(i18n.t("propertyWatch.validation.nameRequired"));
  else if (draft.name.trim().length > 80) {
    reasons.push(i18n.t("propertyWatch.validation.nameLength"));
  }
  if (draft.description.trim().length > 2000) {
    reasons.push(i18n.t("propertyWatch.validation.descriptionLength"));
  }
  if (!draft.scopeId) {
    reasons.push(
      i18n.t("propertyWatch.validation.chooseScope", {
        kind: authoringScopeKindLabel(draft.scopeKind, i18n).toLocaleLowerCase(
          i18n.locale === "tr" ? "tr-TR" : "en-US",
        ),
      }),
    );
  }
  if (draft.properties.length < 1 || draft.properties.length > catalog.max_properties) {
    reasons.push(
      i18n.t("propertyWatch.validation.propertyCount", {
        max: formatCount(catalog.max_properties, i18n.locale),
      }),
    );
  }
  for (const property of draft.properties) {
    const reason = propertyReason(property, i18n);
    if (reason) reasons.push(reason);
  }
  if (new Set(draft.properties.map((p) => `${p.kind}:${p.name}`)).size !== draft.properties.length) {
    reasons.push(i18n.t("propertyWatch.validation.duplicateProperty"));
  }
  if (draft.pins.length > catalog.max_pinned_values) {
    reasons.push(
      i18n.t("propertyWatch.validation.pinCount", {
        max: formatCount(catalog.max_pinned_values, i18n.locale),
      }),
    );
  }
  draft.pins.forEach((pin, pinIndex) => {
    if (pin.length !== draft.properties.length) {
      reasons.push(
        i18n.t("propertyWatch.validation.pinParts", {
          index: formatCount(pinIndex + 1, i18n.locale),
        }),
      );
      return;
    }
    pin.forEach((value) => {
      const reason = pinReason(value, catalog.value_max_length, i18n);
      if (reason) reasons.push(reason);
    });
  });
  if (new Set(draft.pins.map((pin) => JSON.stringify(pin))).size !== draft.pins.length) {
    reasons.push(i18n.t("propertyWatch.validation.duplicatePin"));
  }
  if (draft.detectors.length === 0) {
    reasons.push(i18n.t("propertyWatch.validation.chooseDetector"));
  }
  if (draft.properties.length !== 2 && draft.detectors.includes("PW_NEW_COMBINATION")) {
    reasons.push(i18n.t("propertyWatch.validation.combinationProperties"));
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

export function watchSummary(draft: WatchDraft, i18n: PwI18n = ENGLISH_PW): string {
  const properties = draft.properties
    .map((p) => p.name || i18n.t("propertyWatch.authoring.unnamedProperty"))
    .join(" × ");
  const kind = authoringScopeKindLabel(draft.scopeKind, i18n).toLocaleLowerCase(
    i18n.locale === "tr" ? "tr-TR" : "en-US",
  );
  const scope = draft.scopeLabel || i18n.t("propertyWatch.authoring.unselectedScope", { kind });
  const count =
    draft.scopeCount === null
      ? ""
      : i18n.t("propertyWatch.authoring.scopeCount", { count: draft.scopeCount });
  const detection =
    draft.detectors
      .map((d) =>
        detectorCopy(d, i18n).label.toLocaleLowerCase(i18n.locale === "tr" ? "tr-TR" : "en-US"),
      )
      .join(", ") || i18n.t("propertyWatch.authoring.noDetection");
  const history =
    draft.historyDays > 0
      ? i18n.t("propertyWatch.authoring.historyFirst", { days: draft.historyDays })
      : "";
  return i18n.t("propertyWatch.authoring.summary", {
    properties,
    scope,
    count,
    detection,
    history,
    pins: draft.pins.length,
  });
}

export function propertyApplicability(
  property: PwPropertyRef,
  options: PwPropertyOptions | null,
  i18n: PwI18n = ENGLISH_PW,
): string {
  if (property.kind === "NORMALIZED") return i18n.t("propertyWatch.authoring.normalized");
  const option = options?.properties.find((row) => row.name === property.name);
  if (!option) return i18n.t("propertyWatch.authoring.notInCatalog");
  return i18n.t("propertyWatch.authoring.definedTypes", {
    defined: formatCount(option.defined_on_type_ids.length, i18n.locale),
    total: formatCount(options?.scope_type_ids.length ?? 0, i18n.locale),
  });
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
  | { kind: "conflict"; detail: string; codes: string[] }
  | { kind: "error"; detail: string };

/** The code of the capacity refusal that only the history budget causes (T-145). */
export const HISTORY_BUDGET_CODE = "HISTORY_BUDGET";

/**
 * Whether the refusal is the history budget and nothing else.
 *
 * Only then can the operator enable the watch without history: another reason (the tick
 * budget, the enabled-watch cap) refuses the same attempt, and the offer would fail
 * again. The codes are read, never the wording, so a reworded reason cannot change what
 * the operator is offered. A refusal without codes (an older server) is never one.
 */
export function onlyHistoryBudget(codes: string[]): boolean {
  return codes.length === 1 && codes[0] === HISTORY_BUDGET_CODE;
}

/** The refusal's first sentence in the reader's language (the server's own text in English). */
function said(error: ApiError, i18n: PwI18n): string | null {
  const [sentence] = refusalSentences(error);
  return sentence ? renderBackendMessage(sentence, i18n.locale, i18n.t) : null;
}

export function classifyWatchError(
  error: unknown,
  i18n: PwI18n = ENGLISH_PW,
): WatchWriteRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: "error", detail: i18n.t("propertyWatch.authoring.saveFailed") };
  }
  const detail = record(error.detail);
  if (error.status === 422 && Array.isArray(detail?.reasons)) {
    // `reason_codes` runs parallel to `reasons`; an item with no code shows the server's text.
    const codes = Array.isArray(detail.reason_codes) ? detail.reason_codes : [];
    return {
      kind: "reasons",
      reasons: detail.reasons.flatMap((reason, index) => {
        if (typeof reason !== "string") return [];
        const coded = record(codes[index]);
        return [
          renderBackendMessage(
            {
              text: reason,
              code: typeof coded?.code === "string" ? coded.code : null,
              params: record(coded?.params),
            },
            i18n.locale,
            i18n.t,
          ),
        ];
      }),
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
      message: said(error, i18n) ?? i18n.t("propertyWatch.authoring.dependencyFallback"),
      rules: detail.rules.flatMap((item) => {
        const rule = record(item);
        return typeof rule?.id === "string" && typeof rule.name === "string"
          ? [{ id: rule.id, name: rule.name }]
          : [];
      }),
    };
  }
  const text = typeof error.detail === "string" ? (said(error, i18n) ?? error.detail) : error.message;
  if (error.status === 403 && i18n.locale === "tr") {
    return { kind: "conflict", detail: i18n.t("propertyWatch.authoring.writeForbidden"), codes: [] };
  }
  if (error.status === 429) return { kind: "rate", detail: text };
  if (error.status === 409) return { kind: "conflict", detail: text, codes: error.codes };
  return { kind: "error", detail: i18n.t("propertyWatch.authoring.saveBackendFailed") };
}
