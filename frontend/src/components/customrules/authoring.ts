import {
  ApiError,
  type CallerInfo,
  type CustomRule,
  type CustomRuleCatalog,
  type CustomRuleCatalogMetric,
  type CustomRuleCreate,
  type CustomRuleDraft,
  type CustomRuleSubjectKind,
  type CustomRuleUpdate,
} from "@/lib/api";
import { latencyParts, type LatencyCadence, type LatencyParts } from "@/components/cadence/CadenceLatency";

export const CUSTOM_RULE_WRITE = "custom_rule:write";

/** Said once per page. The delay is a number only when the page holds the family's cadence. */
export function customRuleLatency(cadence?: LatencyCadence | null): LatencyParts {
  return latencyParts(
    "Rules",
    cadence,
    "This is not a seconds-level correlation engine — for alerting within seconds, use QRadar's own rules.",
  );
}

export type CustomRuleAccess = "writer" | "reader" | "unknown";

export function customRuleAccessFrom(caller: CallerInfo): CustomRuleAccess {
  const writer =
    caller.effective_permissions.includes(CUSTOM_RULE_WRITE) ||
    caller.permissions.includes(CUSTOM_RULE_WRITE) ||
    caller.permissions.includes("admin:*");
  return writer ? "writer" : "reader";
}

export const SUBJECT_KIND_LABELS: Record<CustomRuleSubjectKind, string> = {
  LOG_SOURCE: "Log source",
  FLEET_CLASS: "Fleet class",
  WAF_ENDPOINT: "WAF endpoint",
  IPS_ENTITY: "IPS entity",
  IPS_SOURCE: "IPS source",
  PW_VALUE: "Property watch value",
};

/** A kind label inside a sentence. Acronyms stay uppercase; other labels take a lowercase first word. */
export function subjectKindInSentence(kind: CustomRuleSubjectKind): string {
  const label = SUBJECT_KIND_LABELS[kind];
  return /^[A-Z]{2,}\b/.test(label) ? label : `${label.charAt(0).toLowerCase()}${label.slice(1)}`;
}

const METRIC_LABELS: Record<string, string> = {
  EVENT_COUNT: "event count",
  AVERAGE_EPS: "average EPS",
  EPS: "fleet EPS",
  REQUEST_COUNT: "request count",
  BLOCK_RATIO: "block ratio",
  SERVER_ERROR_RATIO: "5xx ratio",
  CLIENT_ERROR_RATIO: "4xx ratio",
  EVENTS: "event count",
  TOTAL_EVENTS: "total event count",
  BLOCKED_SHARE: "blocked share",
  PW_VALUE_EVENTS: "property-watch value events",
};

const OPERATOR_WORDS: Record<string, string> = {
  GT: "above",
  GE: "at or above",
  LT: "below",
  LE: "at or below",
  EQ: "equal to",
};

const OPERATOR_SIGNS: Record<string, string> = {
  GT: ">",
  GE: "≥",
  LT: "<",
  LE: "≤",
  EQ: "=",
};

export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric.toLowerCase().replaceAll("_", " ");
}

export function operatorWord(operator: string): string {
  return OPERATOR_WORDS[operator] ?? operator;
}

export function operatorSign(operator: string): string {
  return OPERATOR_SIGNS[operator] ?? operator;
}

export function formatValue(value: number, unit: string): string {
  const number = Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
  return `${number} ${unit}`;
}

export function conditionText(rule: Pick<CustomRule, "metric" | "operator" | "threshold_value" | "threshold_unit" | "sustain_buckets">): string {
  const unit = rule.threshold_unit ?? "units";
  return `${metricLabel(rule.metric)} ${operatorSign(rule.operator)} ${formatValue(rule.threshold_value, unit)} for ${rule.sustain_buckets} ${rule.sustain_buckets === 1 ? "bucket" : "buckets"}`;
}

export function editorSummary(
  operator: string,
  threshold: number,
  unit: string,
  sustain: number,
  recovery: number,
): string {
  const breach = operatorWord(operator);
  const recover = operator === "GT" ? "at or below" : operator === "GE" ? "below" : operator === "LT" ? "at or above" : operator === "LE" ? "above" : "not equal to";
  return `Fires after ${sustain} consecutive ${sustain === 1 ? "bucket" : "buckets"} ${breach} ${formatValue(threshold, unit)}; recovers after ${recovery} ${recovery === 1 ? "bucket" : "buckets"} ${recover}.`;
}

export interface EditorDraft {
  name: string;
  description: string;
  subjectKind: CustomRuleSubjectKind;
  logSourceId: string;
  sourceClass: string;
  wafEndpointId: string;
  ipsEntityId: string;
  pwValueId: string;
  metric: string;
  operator: string;
  threshold: string;
  sustain: string;
  recovery: string;
  severity: string;
  enabled: boolean;
}

export function emptyDraft(catalog: CustomRuleCatalog): EditorDraft {
  const subjectKind = catalog.subject_kinds[0]?.subject_kind ?? "LOG_SOURCE";
  return {
    name: "",
    description: "",
    subjectKind,
    logSourceId: "",
    sourceClass: "",
    wafEndpointId: "",
    ipsEntityId: "",
    pwValueId: "",
    metric: catalog.metrics.find((metric) => metric.subject_kind === subjectKind)?.metric ?? "",
    operator: catalog.operators.includes("GT") ? "GT" : (catalog.operators[0] ?? "GT"),
    threshold: "",
    sustain: "2",
    recovery: "2",
    severity: "MEDIUM",
    enabled: false,
  };
}

export function draftFromRule(rule: CustomRule): EditorDraft {
  return {
    name: rule.name,
    description: rule.description ?? "",
    subjectKind: rule.subject_kind,
    logSourceId: rule.log_source_id ?? "",
    sourceClass: rule.source_class ?? "",
    wafEndpointId: rule.waf_endpoint_id ?? "",
    ipsEntityId: rule.ips_entity_id ?? "",
    pwValueId: rule.pw_value_id ?? "",
    metric: rule.metric,
    operator: rule.operator,
    threshold: String(rule.threshold_value),
    sustain: String(rule.sustain_buckets),
    recovery: String(rule.recovery_buckets),
    severity: rule.severity,
    enabled: rule.enabled,
  };
}

export function metricsFor(catalog: CustomRuleCatalog, kind: CustomRuleSubjectKind): CustomRuleCatalogMetric[] {
  return catalog.metrics.filter((metric) => metric.subject_kind === kind);
}

export function selectedMetric(catalog: CustomRuleCatalog, draft: EditorDraft): CustomRuleCatalogMetric | null {
  return catalog.metrics.find((metric) => metric.metric === draft.metric) ?? null;
}

function positiveInteger(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const parsed = Number(value);
  return parsed >= 1 ? parsed : null;
}

export function draftReason(draft: EditorDraft, metric: CustomRuleCatalogMetric | null): string | null {
  if (draft.name.trim() === "") return "Name is required.";
  if (draft.name.trim().length > 80) return "Name must be 80 characters or fewer.";
  if (draft.description.trim().length > 2000) return "Description must be 2,000 characters or fewer.";
  const selected =
    draft.subjectKind === "FLEET_CLASS"
      ? draft.sourceClass
      : draft.subjectKind === "WAF_ENDPOINT"
        ? draft.wafEndpointId
        : draft.subjectKind === "IPS_ENTITY"
          ? draft.ipsEntityId
        : draft.subjectKind === "PW_VALUE"
          ? draft.pwValueId
          : draft.logSourceId;
  if (!selected) return `Choose a ${subjectKindInSentence(draft.subjectKind)}.`;
  if (!metric || metric.subject_kind !== draft.subjectKind) return "Choose a metric for this subject kind.";
  const threshold = Number(draft.threshold);
  if (draft.threshold.trim() === "" || !Number.isFinite(threshold) || threshold < 0) {
    return "Threshold must be a number greater than or equal to zero.";
  }
  if (metric.is_ratio && (threshold <= 0 || threshold >= 1)) {
    return "A share threshold must be greater than zero and less than one.";
  }
  if (positiveInteger(draft.sustain) === null) return "Sustain must be at least one bucket.";
  if (positiveInteger(draft.recovery) === null) return "Recovery must be at least one bucket.";
  return null;
}

export function previewBody(draft: EditorDraft): CustomRuleDraft {
  return {
    subject_kind: draft.subjectKind,
    log_source_id: draft.subjectKind === "LOG_SOURCE" || draft.subjectKind === "IPS_SOURCE" ? draft.logSourceId || null : null,
    source_class: draft.subjectKind === "FLEET_CLASS" ? draft.sourceClass || null : null,
    waf_endpoint_id: draft.subjectKind === "WAF_ENDPOINT" ? draft.wafEndpointId || null : null,
    ips_entity_id: draft.subjectKind === "IPS_ENTITY" ? draft.ipsEntityId || null : null,
    pw_value_id: draft.subjectKind === "PW_VALUE" ? draft.pwValueId || null : null,
    metric: draft.metric,
    operator: draft.operator,
    threshold_value: Number(draft.threshold),
    sustain_buckets: Number(draft.sustain),
    recovery_buckets: Number(draft.recovery),
    severity: draft.severity,
    enabled: draft.enabled,
  };
}

export function createBody(draft: EditorDraft): CustomRuleCreate {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    ...previewBody(draft),
  };
}

export function updateBody(draft: EditorDraft, rule: CustomRule): CustomRuleUpdate {
  return { expected_updated_at: rule.updated_at, ...createBody(draft) };
}

export type SaveRefusal =
  | { kind: "reasons"; reasons: string[] }
  | { kind: "cap"; detail: string }
  | { kind: "version"; currentVersion: string; updatedBy: string | null }
  | { kind: "error"; detail: string };

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function classifySaveError(error: unknown): SaveRefusal {
  if (!(error instanceof ApiError)) return { kind: "error", detail: "The rule could not be saved. Try again." };
  const detail = record(error.detail);
  if (error.status === 422 && Array.isArray(detail?.reasons)) {
    return { kind: "reasons", reasons: detail.reasons.filter((reason): reason is string => typeof reason === "string") };
  }
  if (error.status === 409 && typeof error.detail === "string" && error.detail.includes("at most")) {
    return { kind: "cap", detail: error.detail };
  }
  if (error.status === 409 && typeof detail?.current_version === "string") {
    return {
      kind: "version",
      currentVersion: detail.current_version,
      updatedBy: typeof detail.updated_by === "string" ? detail.updated_by : null,
    };
  }
  return { kind: "error", detail: typeof error.detail === "string" ? error.detail : "The rule could not be saved. Try again." };
}

/** Words an analyst reads. Only a recorded OK verdict takes the positive tone. */
const STATUS_WORDS: Record<string, string> = {
  OK: "OK",
  OPEN: "Open",
  RECOVERING: "Recovering",
  RESOLVED: "Resolved",
  NO_VERDICT: "No verdict",
  STALE: "Stale",
  NOT_COLLECTED: "Not collected",
  SUBJECT_RETIRED: "Subject retired",
  DISABLED: "Disabled",
  NOT_ENABLED: "Not enabled",
};

export function statusTone(status: string | null): string {
  if (status === "OPEN") return "crit";
  if (status === "OK") return "ok";
  if (status === "RECOVERING" || status === "STALE") return "warn";
  return "";
}

export function statusLabel(status: string | null): string {
  if (status === null) return "Never evaluated";
  return STATUS_WORDS[status] ?? status.replaceAll("_", " ").toLowerCase().replace(/^\w/, (letter) => letter.toUpperCase());
}

/** The engine's reason, unchanged. Null means the engine recorded none, so the UI shows none. */
export function statusReason(_status: string | null, reason: string | null): string | null {
  const text = reason?.trim() ?? "";
  return text === "" ? null : text;
}
