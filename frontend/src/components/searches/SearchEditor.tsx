"use client";

// The custom AQL editor. Save stores a disabled version. Run now, on the detail
// page, executes that stored version — this form never sends the text to a run.

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { ScheduleLabel } from "@/components/searches/ScheduleLabel";
import {
  CUSTOM_SCHEDULE,
  SEARCH_CAPS,
  SEVERITIES,
  THRESHOLD_OPERATORS,
  capMessage,
  classifySaveError,
  createBody,
  draftFromSearch,
  emptyDraft,
  parseDraft,
  scheduleOptions,
  scheduleSelectValue,
  updateBody,
  visualizationChoices,
  visualizationLabel,
  type EditorDraft,
  type FieldName,
} from "@/components/searches/authoring";
import { enumLabel } from "@/i18n/enumLabels";
import { api, type ScheduledSearch } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import { cronToPreset } from "@/lib/searchCadence";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";


import "./searches.css";

export function SearchEditor({
  mode,
  search,
}: {
  mode: "create" | "edit";
  search?: ScheduledSearch;
}) {
  const router = useRouter();
  const t = useTranslations("searches.editor");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const nameId = useId();
  const descriptionId = useId();
  const aqlId = useId();
  const reasonsId = useId();
  const scheduleId = useId();
  const scheduleErrorId = useId();
  const operatorId = useId();
  const thresholdId = useId();
  const severityId = useId();
  const timeoutId = useId();
  const windowId = useId();
  const rowsId = useId();
  const visualizationId = useId();
  const noteId = useId();

  const [draft, setDraft] = useState<EditorDraft>(() => (search ? draftFromSearch(search) : emptyDraft()));
  const [originalCron] = useState<string | null>(search && cronToPreset(search.schedule_cron) === null ? search.schedule_cron : null);
  const [pending, setPending] = useState(false);
  const [fieldError, setFieldError] = useState<{ field: FieldName; text: string } | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  function set<K extends keyof EditorDraft>(key: K, value: EditorDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function onSchedule(value: string) {
    if (value === CUSTOM_SCHEDULE) {
      if (originalCron !== null) set("cron", originalCron);
      return;
    }
    set("cron", value);
  }

  async function save() {
    if (pending) return;
    setFieldError(null);
    setReasons([]);
    setScheduleError(null);
    setFormError(null);
    setSaved(null);
    const invalid = parseDraft(draft, mode, i18n);
    if (invalid) {
      setFieldError(invalid);
      return;
    }
    setPending(true);
    try {
      if (mode === "create") {
        const created = await api.createSearch(createBody(draft));
        router.push(`/searches/${created.id}`);
        return;
      }
      if (!search) return;
      const updated = await api.updateSearch(search.id, updateBody(draft));
      const versionNote =
        updated.query_version === search.query_version
          ? t("savedSame")
          : t("savedNew", { version: updated.query_version });
      setSaved(versionNote);
      set("aql", updated.aql_query);
      set("cron", updated.schedule_cron);
      router.refresh();
    } catch (err) {
      const refusal = classifySaveError(err, i18n);
      if (refusal.kind === "reasons") setReasons(refusal.reasons);
      else if (refusal.kind === "schedule") setScheduleError(refusal.detail);
      else if (refusal.kind === "cap") setFormError(capMessage(refusal.detail, i18n));
      else setFormError(refusal.detail);
    } finally {
      setPending(false);
    }
  }

  const options = scheduleOptions(draft.cron, originalCron, i18n);
  const selectValue = scheduleSelectValue(draft.cron);
  const charts = visualizationChoices(search?.visualization_type ?? null);
  const storedChart = search?.visualization_type;
  const unusualChart = storedChart === "PIE" || storedChart === "HEATMAP";

  return (
    <form
      className="rule-form search-editor"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p className="search-latency">{t("latency")}</p>

      {mode === "create" && (
        <div className="rule-form-grid">
          <label htmlFor={nameId}>{t("name")}</label>
          <div>
            <input
              id={nameId}
              className="field"
              value={draft.name}
              maxLength={255}
              autoComplete="off"
              aria-invalid={fieldError?.field === "name"}
              onChange={(event) => set("name", event.target.value)}
            />
            {fieldError?.field === "name" && (
              <p className="field-error" role="alert">
                {fieldError.text}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="rule-form-grid">
        <label htmlFor={descriptionId}>{t("description")}</label>
        <div>
          <input
            id={descriptionId}
            className="field"
            value={draft.description}
            maxLength={2000}
            autoComplete="off"
            aria-invalid={fieldError?.field === "description"}
            onChange={(event) => set("description", event.target.value)}
          />
          {fieldError?.field === "description" && (
            <p className="field-error" role="alert">
              {fieldError.text}
            </p>
          )}
        </div>
      </div>

      <div>
        <label htmlFor={aqlId}>{t("aql")}</label>
        <textarea
          id={aqlId}
          className="aql-input"
          value={draft.aql}
          spellCheck={false}
          autoComplete="off"
          aria-invalid={reasons.length > 0 || fieldError?.field === "aql"}
          aria-describedby={reasons.length > 0 ? reasonsId : undefined}
          onChange={(event) => set("aql", event.target.value)}
        />
        {fieldError?.field === "aql" && (
          <p className="field-error" role="alert">
            {fieldError.text}
          </p>
        )}
        {reasons.length > 0 && (
          <ul id={reasonsId} className="search-reasons" aria-label={t("validationReasons")} aria-live="polite">
            {reasons.map((reason, index) => (
              <li key={`${index}-${reason}`}>{reason}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="rule-form-grid">
        <label htmlFor={scheduleId}>{t("schedule")}</label>
        <div>
          <p className="field-hint">
            {mode === "create" ? t("scheduleDefault") : t("scheduleCurrent")}
            <ScheduleLabel cron={draft.cron} />
          </p>
          <select
            id={scheduleId}
            className="select-control"
            value={selectValue}
            aria-invalid={scheduleError !== null}
            aria-describedby={scheduleError ? scheduleErrorId : undefined}
            onChange={(event) => onSchedule(event.target.value)}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {selectValue === CUSTOM_SCHEDULE && (
            <p className="field-hint">
              {t.rich("customCron", {
                code: () => <code className="search-cron">{draft.cron}</code>,
              })}
            </p>
          )}
          {scheduleError && (
            <p id={scheduleErrorId} className="field-error" role="alert">
              {scheduleError}
            </p>
          )}
        </div>
      </div>

      <div className="rule-form-grid">
        <span id={`${operatorId}-label`}>{t("threshold")}</span>
        <div className="search-guard" role="group" aria-labelledby={`${operatorId}-label`}>
          <label htmlFor={operatorId}>
            {t("operator")}
            <select
              id={operatorId}
              className="select-control"
              value={draft.operator}
              onChange={(event) => set("operator", event.target.value)}
            >
              {THRESHOLD_OPERATORS.map((operator) => (
                <option key={operator} value={operator}>
                  {operator}
                </option>
              ))}
              {THRESHOLD_OPERATORS.every((operator) => operator !== draft.operator) && (
                <option value={draft.operator}>{draft.operator}</option>
              )}
            </select>
          </label>
          <label htmlFor={thresholdId}>
            {t("value")}
            <input
              id={thresholdId}
              className="field"
              inputMode="decimal"
              value={draft.threshold}
              aria-invalid={fieldError?.field === "threshold"}
              onChange={(event) => set("threshold", event.target.value)}
            />
          </label>
        </div>
      </div>
      {fieldError?.field === "threshold" && (
        <p className="field-error" role="alert">
          {fieldError.text}
        </p>
      )}
      <p className="field-hint">{t("thresholdHint")}</p>

      <div className="rule-form-grid">
        <label htmlFor={severityId}>{t("severity")}</label>
        <select
          id={severityId}
          className="select-control"
          value={draft.severity}
          onChange={(event) => set("severity", event.target.value)}
        >
          {SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {enumLabel(i18n.t, "severityPill", severity)}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="search-guard">
        <legend className="field-hint">{t("guardrails")}</legend>
        <label htmlFor={timeoutId}>
          {t("timeout")}
          <input
            id={timeoutId}
            className="field"
            inputMode="numeric"
            value={draft.timeout}
            aria-invalid={fieldError?.field === "timeout"}
            onChange={(event) => set("timeout", event.target.value)}
          />
          <span className="field-hint">
            {t("timeoutHint", { max: plainCount(SEARCH_CAPS.timeoutSeconds.max, i18n.locale) })}
          </span>
        </label>
        <label htmlFor={windowId}>
          {t("window")}
          <input
            id={windowId}
            className="field"
            inputMode="numeric"
            value={draft.windowHours}
            aria-invalid={fieldError?.field === "window"}
            onChange={(event) => set("windowHours", event.target.value)}
          />
          <span className="field-hint">
            {t("windowHint", { max: plainCount(SEARCH_CAPS.windowHours.max, i18n.locale) })}
          </span>
        </label>
        <label htmlFor={rowsId}>
          {t("rows")}
          <input
            id={rowsId}
            className="field"
            inputMode="numeric"
            value={draft.rows}
            aria-invalid={fieldError?.field === "rows"}
            onChange={(event) => set("rows", event.target.value)}
          />
          <span className="field-hint">
            {t("rowsHint", { max: plainCount(SEARCH_CAPS.resultRows.max, i18n.locale) })}
          </span>
        </label>
      </fieldset>
      {fieldError && (fieldError.field === "timeout" || fieldError.field === "window" || fieldError.field === "rows") && (
        <p className="field-error" role="alert">
          {fieldError.text}
        </p>
      )}

      <div className="rule-form-grid">
        <label htmlFor={visualizationId}>{t("visualization")}</label>
        <div>
          <select
            id={visualizationId}
            className="select-control"
            value={draft.visualization}
            onChange={(event) => set("visualization", event.target.value)}
          >
            {charts.map((value) => (
              <option key={value} value={value}>
                {visualizationLabel(value, i18n)}
              </option>
            ))}
          </select>
          {unusualChart && draft.visualization === storedChart && (
            <p className="field-hint">
              {t("storedAs", { stored: storedChart ?? "" })}
            </p>
          )}
        </div>
      </div>

      {mode === "edit" && (
        <div className="rule-form-grid">
          <label htmlFor={noteId}>{t("changeNote")}</label>
          <div>
            <input
              id={noteId}
              className="field"
              value={draft.changeNote}
              maxLength={1000}
              aria-invalid={fieldError?.field === "note"}
              onChange={(event) => set("changeNote", event.target.value)}
            />
            <p className="field-hint">{t("changeNoteHint")}</p>
            {fieldError?.field === "note" && (
              <p className="field-error" role="alert">
                {fieldError.text}
              </p>
            )}
          </div>
        </div>
      )}

      {formError && (
        <p className="banner crit" role="alert">
          {formError}
        </p>
      )}
      {saved && (
        <p className="change-status ok" role="status">
          {saved}
        </p>
      )}

      <div className="actions">
        <button type="submit" className="action" disabled={pending} aria-busy={pending}>
          {pending ? common("saving") : mode === "create" ? t("saveCreate") : t("saveEdit")}
        </button>
      </div>
    </form>
  );
}
