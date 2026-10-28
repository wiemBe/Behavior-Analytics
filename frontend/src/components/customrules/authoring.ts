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
import { latencyCadence, type LatencyCadence, type LatencyParts } from "@/components/cadence/CadenceLatency";
import type { Locale } from "@/i18n/locale";
import type { TextTranslator } from "@/i18n/textTranslator";
import { formatCount } from "@/lib/format";

import { customRulesEnglishText } from "./englishText";

export const CUSTOM_RULE_WRITE = "custom_rule:write";

/** Said once per page. The delay is a number only when the page holds the family's cadence. */
export function customRuleLatency(
  cadence?: LatencyCadence | null,
  locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): LatencyParts {
  const delay = customRuleDelayText(cadence, locale, t);
  return {
    lead: delay
      ? t("customRules.latency.knownLead", { delay })
      : t("customRules.latency.unknownLead"),
    tail: t("customRules.latency.tail", { closing: t("customRules.latency.closing") }),
  };
}

/**
 * The sentence for a catalog: each subject kind's delay comes from the cadence of the family it is
 * collected under. When every kind gives one figure it is said once; when they differ, each figure
 * names its kinds. A kind whose family has no cadence in the catalog makes the whole sentence
 * figure-free: a number is never guessed for it.
 */
export function customRuleLatencyFor(
  catalog: CustomRuleCatalog | null,
  locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): LatencyParts {
  const cadences = catalog?.cadences;
  const kinds = catalog?.subject_kinds ?? [];
  if (!cadences || kinds.length === 0) return customRuleLatency(undefined, locale, t);
  const groups = new Map<string, string[]>();
  for (const kind of kinds) {
    const cadence = cadences.find((c) => c.family === kind.search_family);
    const duration = customRuleDelayText(latencyCadence(cadence), locale, t);
    const delay = duration ? t("customRules.latency.delay", { duration }) : null;
    if (!delay) return customRuleLatency(undefined, locale, t);
    groups.set(delay, [...(groups.get(delay) ?? []), subjectKindLabel(kind.subject_kind, t)]);
  }
  if (groups.size === 1) {
    return customRuleLatency(
      latencyCadence(cadences.find((c) => c.family === kinds[0].search_family)),
      locale,
      t,
    );
  }
  const by = [...groups]
    .map(([delay, labels]) => t("customRules.latency.group", { labels: labels.join(", "), delay }))
    .join("; ");
  return {
    lead: t("customRules.latency.byLead", { groups: by }),
    tail: t("customRules.latency.tail", { closing: t("customRules.latency.closing") }),
  };
}

export function customRuleDelayText(
  cadence: LatencyCadence | null | undefined,
  locale: Locale,
  t: TextTranslator,
): string | null {
  if (!cadence) return null;
  const seconds = cadence.cadenceSeconds + (cadence.bucketSeconds ?? 0) + cadence.settleSeconds;
  const parts: string[] = [];
  const values = [
    ["durationDay", Math.floor(seconds / 86400)],
    ["durationHour", Math.floor((seconds % 86400) / 3600)],
    ["durationMinute", Math.floor((seconds % 3600) / 60)],
    ["durationSecond", seconds % 60],
  ] as const;
  for (const [key, value] of values) {
    if (value > 0) parts.push(t(`customRules.latency.${key}`, { count: formatCount(value, locale) }));
  }
  return parts.join(" ");
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

export function subjectKindLabel(
  kind: CustomRuleSubjectKind,
  t: TextTranslator = customRulesEnglishText,
): string {
  return t(`customRules.authoring.subjectKinds.${kind}`);
}

/** A kind label inside a sentence. Acronyms stay uppercase; other labels take a lowercase first word. */
export function subjectKindInSentence(
  kind: CustomRuleSubjectKind,
  t: TextTranslator = customRulesEnglishText,
): string {
  const label = subjectKindLabel(kind, t);
  return /^[A-Z]{2,}\b/.test(label)
    ? label
    : `${label.charAt(0).toLocaleLowerCase()}${label.slice(1)}`;
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

export function metricLabel(metric: string, t: TextTranslator = customRulesEnglishText): string {
  return Object.hasOwn(METRIC_LABELS, metric) ? t(`customRules.authoring.metrics.${metric}`) : metric;
}

export function operatorWord(operator: string, t: TextTranslator = customRulesEnglishText): string {
  return Object.hasOwn(OPERATOR_WORDS, operator) ? t(`customRules.authoring.operators.${operator}`) : operator;
}

export function operatorSign(operator: string): string {
  return OPERATOR_SIGNS[operator] ?? operator;
}

export function formatValue(value: number, unit: string, locale: Locale = "en"): string {
  const number = locale === "en"
    ? Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
    : value.toLocaleString("tr-TR", { maximumFractionDigits: 4 });
  return `${number}${locale === "tr" ? "\u00a0" : " "}${unit}`;
}

export function conditionText(
  rule: Pick<CustomRule, "metric" | "operator" | "threshold_value" | "threshold_unit" | "sustain_buckets">,
  locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): string {
  const unit = rule.threshold_unit ?? "units";
  return t("customRules.authoring.condition", {
    metric: metricLabel(rule.metric, t),
    operator: operatorSign(rule.operator),
    value: formatValue(rule.threshold_value, unit, locale),
    count: rule.sustain_buckets,
  });
}

export function editorSummary(
  operator: string,
  threshold: number,
  unit: string,
  sustain: number,
  recovery: number,
  locale: Locale = "en",
  t: TextTranslator = customRulesEnglishText,
): string {
  const breach = operatorWord(operator, t);
  const recoverKey = operator === "GT" ? "recoverGT" : operator === "GE" ? "recoverGE" : operator === "LT" ? "recoverLT" : operator === "LE" ? "recoverLE" : "recoverEQ";
  return t("customRules.authoring.summary", {
    sustain,
    breach,
    value: formatValue(threshold, unit, locale),
    recovery,
    recover: t(`customRules.authoring.operators.${recoverKey}`),
  });
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

export function draftReason(
  draft: EditorDraft,
  metric: CustomRuleCatalogMetric | null,
  t: TextTranslator = customRulesEnglishText,
): string | null {
  if (draft.name.trim() === "") return t("customRules.authoring.validation.nameRequired");
  if (draft.name.trim().length > 80) return t("customRules.authoring.validation.nameLength");
  if (draft.description.trim().length > 2000) return t("customRules.authoring.validation.descriptionLength");
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
  if (!selected) return t("customRules.authoring.validation.chooseSubject", { subject: subjectKindInSentence(draft.subjectKind, t) });
  if (!metric || metric.subject_kind !== draft.subjectKind) return t("customRules.authoring.validation.chooseMetric");
  const threshold = Number(draft.threshold);
  if (draft.threshold.trim() === "" || !Number.isFinite(threshold) || threshold < 0) {
    return t("customRules.authoring.validation.threshold");
  }
  if (metric.is_ratio && (threshold <= 0 || threshold >= 1)) {
    return t("customRules.authoring.validation.share");
  }
  if (positiveInteger(draft.sustain) === null) return t("customRules.authoring.validation.sustain");
  if (positiveInteger(draft.recovery) === null) return t("customRules.authoring.validation.recovery");
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

export function classifySaveError(
  error: unknown,
  t: TextTranslator = customRulesEnglishText,
): SaveRefusal {
  if (!(error instanceof ApiError)) return { kind: "error", detail: t("customRules.authoring.saveFailed") };
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
  return { kind: "error", detail: typeof error.detail === "string" ? error.detail : t("customRules.authoring.saveFailed") };
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
  INSUFFICIENT_DATA: "Insufficient data",
  NORMAL: "Normal",
};

export function statusTone(status: string | null): string {
  if (status === "OPEN") return "crit";
  if (status === "OK") return "ok";
  if (status === "RECOVERING" || status === "STALE") return "warn";
  return "";
}

export function statusLabel(
  status: string | null,
  t: TextTranslator = customRulesEnglishText,
): string {
  if (status === null) return t("customRules.authoring.status.null");
  return Object.hasOwn(STATUS_WORDS, status) ? t(`customRules.authoring.status.${status}`) : status;
}

/** The engine's reason, unchanged. Null means the engine recorded none, so the UI shows none. */
export function statusReason(_status: string | null, reason: string | null): string | null {
  const text = reason?.trim() ?? "";
  return text === "" ? null : text;
}
