"use client";

// The custom AQL editor. Save stores a disabled version. Run now, on the detail
// page, executes that stored version — this form never sends the text to a run.

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import { ScheduleLabel } from "@/components/searches/ScheduleLabel";
import {
  CUSTOM_SCHEDULE,
  SEARCH_CAPS,
  SEARCH_LATENCY_STATEMENT,
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
import { api, type ScheduledSearch } from "@/lib/api";
import { cronToPreset } from "@/lib/searchCadence";

import "./searches.css";

export function SearchEditor({
  mode,
  search,
}: {
  mode: "create" | "edit";
  search?: ScheduledSearch;
}) {
  const router = useRouter();
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
    const invalid = parseDraft(draft, mode);
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
          ? "Saved. The query text is unchanged, so the version stayed the same."
          : `Saved as v${updated.query_version}. Run now executes this stored version.`;
      setSaved(versionNote);
      set("aql", updated.aql_query);
      set("cron", updated.schedule_cron);
      router.refresh();
    } catch (err) {
      const refusal = classifySaveError(err);
      if (refusal.kind === "reasons") setReasons(refusal.reasons);
      else if (refusal.kind === "schedule") setScheduleError(refusal.detail);
      else if (refusal.kind === "cap") setFormError(capMessage(refusal.detail));
      else setFormError(refusal.detail);
    } finally {
      setPending(false);
    }
  }

  const options = scheduleOptions(draft.cron, originalCron);
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
      <p className="search-latency">{SEARCH_LATENCY_STATEMENT}</p>

      {mode === "create" && (
        <div className="rule-form-grid">
          <label htmlFor={nameId}>Name</label>
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
        <label htmlFor={descriptionId}>Description</label>
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
        <label htmlFor={aqlId}>AQL</label>
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
          <ul id={reasonsId} className="search-reasons" aria-label="Validation reasons" aria-live="polite">
            {reasons.map((reason, index) => (
              <li key={`${index}-${reason}`}>{reason}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="rule-form-grid">
        <label htmlFor={scheduleId}>Schedule</label>
        <div>
          <p className="field-hint">
            {mode === "create" ? "Default: " : "Current: "}
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
              Custom cron <code className="search-cron">{draft.cron}</code> is kept as stored.
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
        <span id={`${operatorId}-label`}>Threshold</span>
        <div className="search-guard" role="group" aria-labelledby={`${operatorId}-label`}>
          <label htmlFor={operatorId}>
            Operator
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
            Value
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
      <p className="field-hint">Leave the value empty when this search has no threshold.</p>

      <div className="rule-form-grid">
        <label htmlFor={severityId}>Severity</label>
        <select
          id={severityId}
          className="select-control"
          value={draft.severity}
          onChange={(event) => set("severity", event.target.value)}
        >
          {SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {severity}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="search-guard">
        <legend className="field-hint">Guardrails, within the platform caps</legend>
        <label htmlFor={timeoutId}>
          Timeout (seconds)
          <input
            id={timeoutId}
            className="field"
            inputMode="numeric"
            value={draft.timeout}
            aria-invalid={fieldError?.field === "timeout"}
            onChange={(event) => set("timeout", event.target.value)}
          />
          <span className="field-hint">1 to {SEARCH_CAPS.timeoutSeconds.max} seconds</span>
        </label>
        <label htmlFor={windowId}>
          Window (hours)
          <input
            id={windowId}
            className="field"
            inputMode="numeric"
            value={draft.windowHours}
            aria-invalid={fieldError?.field === "window"}
            onChange={(event) => set("windowHours", event.target.value)}
          />
          <span className="field-hint">1 to {SEARCH_CAPS.windowHours.max} hours</span>
        </label>
        <label htmlFor={rowsId}>
          Max rows
          <input
            id={rowsId}
            className="field"
            inputMode="numeric"
            value={draft.rows}
            aria-invalid={fieldError?.field === "rows"}
            onChange={(event) => set("rows", event.target.value)}
          />
          <span className="field-hint">1 to {SEARCH_CAPS.resultRows.max} rows</span>
        </label>
      </fieldset>
      {fieldError && (fieldError.field === "timeout" || fieldError.field === "window" || fieldError.field === "rows") && (
        <p className="field-error" role="alert">
          {fieldError.text}
        </p>
      )}

      <div className="rule-form-grid">
        <label htmlFor={visualizationId}>Visualization</label>
        <div>
          <select
            id={visualizationId}
            className="select-control"
            value={draft.visualization}
            onChange={(event) => set("visualization", event.target.value)}
          >
            {charts.map((value) => (
              <option key={value} value={value}>
                {visualizationLabel(value)}
              </option>
            ))}
          </select>
          {unusualChart && draft.visualization === storedChart && (
            <p className="field-hint">
              Stored as {storedChart}. Pie and heatmap are not offered for a new search. Leave this
              value, or choose table, line, bar, or single value.
            </p>
          )}
        </div>
      </div>

      {mode === "edit" && (
        <div className="rule-form-grid">
          <label htmlFor={noteId}>Change note</label>
          <div>
            <input
              id={noteId}
              className="field"
              value={draft.changeNote}
              maxLength={1000}
              aria-invalid={fieldError?.field === "note"}
              onChange={(event) => set("changeNote", event.target.value)}
            />
            <p className="field-hint">Recorded when the query text changes and a new version is stored.</p>
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
          {pending ? "Saving…" : mode === "create" ? "Save as disabled" : "Save changes"}
        </button>
      </div>
    </form>
  );
}
