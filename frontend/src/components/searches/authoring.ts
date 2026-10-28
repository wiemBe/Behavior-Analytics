// Pure helpers for the custom-AQL editor (docs/PHASE-F-CUSTOM-DETECTIONS.md §F.8).
//
// The browser never runs AQL. These functions only shape the stored definition
// and read the API's refusal. A reason string is copied through unchanged.

import { ENGLISH_TREND } from "@/components/trendText";
import { renderRefusal } from "@/i18n/refusalMessage";
import type { TextTranslator } from "@/i18n/textTranslator";
import { ApiError, type CallerInfo, type ScheduledSearch, type ScheduledSearchCreate, type ScheduledSearchUpdate, type SearchExecution, type SearchVersion } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { plainCount } from "@/lib/figureText";
import type { FormatLocale } from "@/lib/format";
import { reasonSentences, refusalSentences } from "@/lib/refusal";
import { SCHEDULE_PRESETS, cronToPreset, everyLabel } from "@/lib/searchCadence";
import { resultCount } from "@/lib/searchRuns";

import { ENGLISH_SEARCHES } from "./searchesText";
import { isPlainKey } from "@/i18n/ownKey";

/** Platform caps (app/core/config.py: ariel_max_timeout_seconds and the two beside it). */
export const SEARCH_CAPS = {
  timeoutSeconds: { min: 1, max: 900, default: 300 },
  windowHours: { min: 1, max: 168, default: 24 },
  resultRows: { min: 1, max: 10_000, default: 10_000 },
} as const;

/** Consecutive failed runs before a search_failure alert (search_failure_alert_after). */
export const SEARCH_FAILURE_ALERT_AFTER = 3;

/**
 * Once on the editor. A scheduled search is one Ariel run on its own cron. The English of the
 * `searches.editor.latency` message (a test pins that they are equal); the editor reads the message.
 */
export const SEARCH_LATENCY_STATEMENT =
  "This search runs in QRadar on its schedule; its result and any threshold breach appear after each run completes, not in real time. For alerting within seconds, use QRadar's own rules.";

export const SEARCH_WRITE = "search:write";

/** A new search defaults to every five minutes (D-28). */
export const DEFAULT_SCHEDULE_CRON = "*/5 * * * *";

export const THRESHOLD_OPERATORS = ["GT", "GE", "LT", "LE", "EQ"] as const;

export const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;

/** Offered by the editor. PIE and HEATMAP stay only when a search already stores one. */
export const VISUALIZATION_OFFERED = ["TABLE", "LINE", "BAR", "SINGLE_VALUE"] as const;

export function visualizationLabel(value: string, i18n: BehaviorI18n = ENGLISH_SEARCHES): string {
  const key = `searches.visualization.${value}`;
  const plain = value !== "" && !value.includes(".") && !(value in Object.prototype);
  return plain && i18n.t.has?.(key) ? i18n.t(key) : value;
}

export function visualizationChoices(stored: string | null): string[] {
  const offered: string[] = [...VISUALIZATION_OFFERED];
  if (stored && !offered.includes(stored)) return [stored, ...offered];
  return offered;
}

export type SearchAccess = "writer" | "reader" | "unknown";

export function searchAccessFrom(caller: CallerInfo): SearchAccess {
  const write =
    caller.effective_permissions.includes(SEARCH_WRITE) ||
    caller.permissions.includes(SEARCH_WRITE) ||
    caller.permissions.includes("admin:*");
  return write ? "writer" : "reader";
}

/** Select value while the cron matches no preset. Never sent as a cron. */
export const CUSTOM_SCHEDULE = "custom";

export function scheduleSelectValue(cron: string): string {
  return cronToPreset(cron) ? cron : CUSTOM_SCHEDULE;
}

/** Preset crons, plus Custom when `cron` (or the stored original) is not a preset. */
export function scheduleOptions(
  cron: string,
  originalCron: string | null,
  i18n: BehaviorI18n = ENGLISH_SEARCHES,
): { value: string; label: string }[] {
  const options: { value: string; label: string }[] = [];
  const custom = cronToPreset(cron) === null || (originalCron !== null && cronToPreset(originalCron) === null);
  if (custom) options.push({ value: CUSTOM_SCHEDULE, label: i18n.t("cadence.schedule.custom") });
  for (const preset of SCHEDULE_PRESETS) {
    options.push({ value: preset.cron, label: everyLabel(preset.seconds, i18n) });
  }
  return options;
}

export interface EditorDraft {
  name: string;
  description: string;
  aql: string;
  cron: string;
  timezone: string;
  operator: string;
  threshold: string;
  severity: string;
  timeout: string;
  windowHours: string;
  rows: string;
  visualization: string;
  changeNote: string;
}

export function emptyDraft(): EditorDraft {
  return {
    name: "",
    description: "",
    aql: "",
    cron: DEFAULT_SCHEDULE_CRON,
    timezone: "UTC",
    operator: "GT",
    threshold: "",
    severity: "MEDIUM",
    timeout: String(SEARCH_CAPS.timeoutSeconds.default),
    windowHours: String(SEARCH_CAPS.windowHours.default),
    rows: String(SEARCH_CAPS.resultRows.default),
    visualization: "TABLE",
    changeNote: "",
  };
}

export function draftFromSearch(search: ScheduledSearch): EditorDraft {
  return {
    name: search.name,
    description: search.description ?? "",
    aql: search.aql_query,
    cron: search.schedule_cron,
    timezone: search.schedule_timezone || "UTC",
    operator: search.threshold_operator,
    threshold: search.threshold_value == null ? "" : String(search.threshold_value),
    severity: search.severity,
    timeout: String(search.timeout_seconds),
    windowHours: String(search.max_time_range_hours),
    rows: String(search.max_result_rows),
    visualization: search.visualization_type,
    changeNote: "",
  };
}

export type FieldName = "name" | "aql" | "threshold" | "timeout" | "window" | "rows" | "note" | "description";

export type FieldError = { field: FieldName; text: string };

function fieldError(
  field: FieldName,
  raw: string,
  min: number,
  max: number,
  unit: "seconds" | "hours" | "rows",
  i18n: BehaviorI18n,
): number | FieldError {
  const text = raw.trim();
  const message = i18n.t("searches.validation.whole", {
    unit: i18n.t(`searches.validation.${unit}`),
    min: plainCount(min, i18n.locale),
    max: plainCount(max, i18n.locale),
  });
  if (!/^\d+$/.test(text)) return { field, text: message };
  const value = Number(text);
  if (value < min || value > max) return { field, text: message };
  return value;
}

export function parseDraft(
  draft: EditorDraft,
  mode: "create" | "edit",
  i18n: BehaviorI18n = ENGLISH_SEARCHES,
): FieldError | null {
  const t = i18n.t;
  if (mode === "create" && draft.name.trim() === "") {
    return { field: "name", text: t("searches.validation.nameRequired") };
  }
  if (mode === "create" && draft.name.trim().length > 255) {
    return { field: "name", text: t("searches.validation.nameLength") };
  }
  if (draft.description.trim().length > 2000) {
    return { field: "description", text: t("searches.validation.descriptionLength") };
  }
  if (draft.aql.trim() === "") return { field: "aql", text: t("searches.validation.aqlRequired") };
  if (draft.changeNote.trim().length > 1000) {
    return { field: "note", text: t("searches.validation.noteLength") };
  }
  if (draft.threshold.trim() !== "" && !/^-?\d+(\.\d+)?$/.test(draft.threshold.trim())) {
    return { field: "threshold", text: t("searches.validation.threshold") };
  }
  const timeout = fieldError("timeout", draft.timeout, SEARCH_CAPS.timeoutSeconds.min, SEARCH_CAPS.timeoutSeconds.max, "seconds", i18n);
  if (typeof timeout !== "number") return timeout;
  const windowHours = fieldError("window", draft.windowHours, SEARCH_CAPS.windowHours.min, SEARCH_CAPS.windowHours.max, "hours", i18n);
  if (typeof windowHours !== "number") return windowHours;
  const rows = fieldError("rows", draft.rows, SEARCH_CAPS.resultRows.min, SEARCH_CAPS.resultRows.max, "rows", i18n);
  if (typeof rows !== "number") return rows;
  return null;
}

function thresholdValue(raw: string): number | null {
  const text = raw.trim();
  if (text === "") return null;
  return Number(text);
}

export function createBody(draft: EditorDraft): ScheduledSearchCreate {
  return {
    name: draft.name.trim(),
    description: draft.description.trim() === "" ? null : draft.description.trim(),
    aql_query: draft.aql,
    schedule_cron: draft.cron,
    schedule_timezone: draft.timezone || "UTC",
    threshold_value: thresholdValue(draft.threshold),
    threshold_operator: draft.operator,
    severity: draft.severity,
    timeout_seconds: Number(draft.timeout.trim()),
    max_time_range_hours: Number(draft.windowHours.trim()),
    max_result_rows: Number(draft.rows.trim()),
    visualization_type: draft.visualization,
    enabled: false,
  };
}

export function updateBody(draft: EditorDraft): ScheduledSearchUpdate {
  return {
    description: draft.description.trim() === "" ? null : draft.description.trim(),
    aql_query: draft.aql,
    schedule_cron: draft.cron,
    schedule_timezone: draft.timezone || "UTC",
    threshold_value: thresholdValue(draft.threshold),
    threshold_operator: draft.operator,
    severity: draft.severity,
    timeout_seconds: Number(draft.timeout.trim()),
    max_time_range_hours: Number(draft.windowHours.trim()),
    max_result_rows: Number(draft.rows.trim()),
    visualization_type: draft.visualization,
    change_note: draft.changeNote.trim() === "" ? null : draft.changeNote.trim(),
  };
}

export type SaveRefusal =
  | { kind: "reasons"; reasons: string[] }
  | { kind: "schedule"; detail: string }
  | { kind: "cap"; detail: string }
  | { kind: "forbidden"; detail: string }
  | { kind: "error"; detail: string };

/** The reasons of a 422 authoring body in the reader's language; the server's text when uncoded. */
function reasonList(err: ApiError, i18n: BehaviorI18n): string[] | null {
  const detail = err.detail;
  if (typeof detail !== "object" || detail === null || !("reasons" in detail)) return null;
  const reasons = (detail as { reasons: unknown }).reasons;
  if (!Array.isArray(reasons) || reasons.some((reason) => typeof reason !== "string")) return null;
  return (reasonSentences(err) ?? []).map((reason) => renderRefusal(reason, i18n.locale, i18n.t));
}

function schemaMessages(detail: unknown): string[] | null {
  if (!Array.isArray(detail)) return null;
  const messages: string[] = [];
  for (const item of detail) {
    if (typeof item === "object" && item !== null && "msg" in item && typeof item.msg === "string") {
      messages.push(item.msg);
    }
  }
  return messages.length > 0 ? messages : null;
}

function isCronFloor(detail: string): boolean {
  return /schedule_cron|seconds apart|cron expression|future fire/i.test(detail);
}

export function classifySaveError(err: unknown, i18n: BehaviorI18n = ENGLISH_SEARCHES): SaveRefusal {
  const t = i18n.t;
  if (!(err instanceof ApiError)) {
    return { kind: "error", detail: t("searches.refusal.network") };
  }
  if (err.status === 409) {
    const detail = typeof err.detail === "string" && err.detail !== "" ? err.detail : err.message;
    const [sentence] = refusalSentences(err);
    return { kind: "cap", detail: sentence ? renderRefusal(sentence, i18n.locale, i18n.t) : detail };
  }
  if (err.status === 403) {
    return { kind: "forbidden", detail: t("searches.refusal.forbidden") };
  }
  if (err.status === 422) {
    const reasons = reasonList(err, i18n);
    if (reasons) return { kind: "reasons", reasons };
    if (typeof err.detail === "string" && isCronFloor(err.detail)) return { kind: "schedule", detail: err.detail };
    if (typeof err.detail === "string" && err.detail !== "") return { kind: "error", detail: err.detail };
    const schema = schemaMessages(err.detail);
    if (schema) return { kind: "reasons", reasons: schema };
  }
  if (err.status === 404) return { kind: "error", detail: t("searches.refusal.gone") };
  if (err.status >= 500) return { kind: "error", detail: t("searches.refusal.server") };
  const fallback =
    typeof err.detail === "string" && err.detail !== "" ? err.detail : t("searches.refusal.notSaved");
  return { kind: "error", detail: fallback };
}

/**
 * A sentence the analyst can read. The limit clause is the server's string,
 * including its number; only a trailing period is trimmed so the sentence has one.
 */
export function capMessage(detail: string, i18n: BehaviorI18n = ENGLISH_SEARCHES): string {
  const limit = detail.trim().replace(/\.\s*$/, "");
  return i18n.t("searches.enable.capped", { limit });
}

/** The newest stored version older than the current one. */
export function previousVersion(versions: SearchVersion[], current: number): SearchVersion | null {
  let best: SearchVersion | null = null;
  for (const version of versions) {
    if (version.version >= current) continue;
    if (best === null || version.version > best.version) best = version;
  }
  return best;
}

/**
 * Line diff of two stored queries, as text. Equal queries return null.
 * A changed query is the only input; the panel renders the result as text.
 */
export function queryDiffText(before: string, after: string): string | null {
  if (before === after) return null;
  const older = before.split("\n");
  const newer = after.split("\n");
  const dp = lcsLengths(older, newer);
  const lines: string[] = [];
  let i = 0;
  let j = 0;
  while (i < older.length && j < newer.length) {
    if (older[i] === newer[j]) {
      lines.push(`  ${older[i]}`);
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push(`- ${older[i]}`);
      i += 1;
    } else {
      lines.push(`+ ${newer[j]}`);
      j += 1;
    }
  }
  while (i < older.length) {
    lines.push(`- ${older[i]}`);
    i += 1;
  }
  while (j < newer.length) {
    lines.push(`+ ${newer[j]}`);
    j += 1;
  }
  return lines.join("\n");
}

function lcsLengths(a: string[], b: string[]): number[][] {
  const rows = a.length;
  const cols = b.length;
  const dp: number[][] = Array.from({ length: rows + 1 }, () => new Array<number>(cols + 1).fill(0));
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

export interface RunReadout {
  tone: "crit" | "warn" | "neutral";
  /** The status in words, including a lower bound when the run was truncated. */
  text: string;
}

/**
 * What a run may claim. A missing count is not zero. A truncated count is a lower bound. The
 * sentences are the `trend.run` messages: `t` is the interface language's translator (full
 * keys); without one they are English, as before. `locale` groups the counts the Turkish way
 * (`1.204`); English prints them plain, as before.
 */
export function runReadout(
  execution: SearchExecution,
  consecutiveFailures: number | null,
  t: TextTranslator = ENGLISH_TREND.t,
  locale: FormatLocale = "en",
): RunReadout {
  const failed = execution.status === "FAILED" || execution.status === "TIMEOUT" || execution.status === "CANCELLED";
  if (failed) {
    const errorKey = execution.error_type ? `trend.run.errors.${execution.error_type}` : null;
    const named = execution.error_type
      ? t("trend.run.errorType", {
          type: execution.error_type,
          words:
            errorKey && isPlainKey(execution.error_type ?? "") && t.has?.(errorKey) ? t(errorKey) : "",
        }).trim()
      : t("trend.run.noErrorType");
    const count =
      consecutiveFailures == null
        ? t("trend.run.countUnknown", { after: SEARCH_FAILURE_ALERT_AFTER })
        : t("trend.run.countKnown", {
            failures: plainCount(consecutiveFailures, locale),
            after: SEARCH_FAILURE_ALERT_AFTER,
          });
    const word =
      execution.status === "FAILED"
        ? t("trend.run.failedWord")
        : t("trend.run.endedWord", { status: execution.status });
    return { tone: "crit", text: t("trend.run.failedPrefix", { word, named, count }) };
  }
  if (execution.truncated) {
    const count = resultCount(execution);
    const bound =
      count.kind === "lower-bound"
        ? t("trend.run.boundAtLeast", { count: plainCount(count.value ?? 0, locale) })
        : t("trend.run.boundNone");
    const verdict = execution.threshold_breached
      ? t("trend.run.verdictMet")
      : t("trend.run.verdictNone");
    return { tone: "warn", text: t("trend.run.truncated", { bound, verdict }) };
  }
  if (execution.status === "COMPLETED") {
    if (execution.result_count == null) {
      return { tone: "neutral", text: t("trend.run.completedNoCount") };
    }
    const breached = execution.threshold_breached ? t("trend.run.breachedTail") : "";
    return {
      tone: "neutral",
      text: t("trend.run.completed", { count: plainCount(execution.result_count, locale), breached }),
    };
  }
  return { tone: "neutral", text: t("trend.run.other", { status: execution.status }) };
}

export function runRequestFailure(err: unknown, i18n: BehaviorI18n = ENGLISH_SEARCHES): string {
  if (err instanceof ApiError && err.status === 403) return i18n.t("searches.run.forbidden");
  if (err instanceof ApiError && err.status === 404) return i18n.t("searches.run.gone");
  return i18n.t("searches.run.failed");
}
