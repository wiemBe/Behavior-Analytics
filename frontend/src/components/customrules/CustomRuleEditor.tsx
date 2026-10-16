"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";

import { PreviewChart, PreviewSummary } from "@/components/customrules/PreviewChart";
import { SubjectPicker } from "@/components/customrules/SubjectPicker";
import {
  classifySaveError,
  createBody,
  draftFromRule,
  draftReason,
  editorSummary,
  emptyDraft,
  metricLabel,
  metricsFor,
  previewBody,
  selectedMetric,
  updateBody,
  type EditorDraft,
} from "@/components/customrules/authoring";
import {
  ApiError,
  api,
  type CustomRule,
  type CustomRuleCatalog,
  type CustomRulePreview,
  type CustomRuleSubjectKind,
} from "@/lib/api";

const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

function previewFailure(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    const detail = error.detail;
    if (typeof detail === "object" && detail !== null && "reasons" in detail) {
      const reasons = (detail as { reasons?: unknown }).reasons;
      if (Array.isArray(reasons)) return reasons.filter((reason) => typeof reason === "string").join(" ");
    }
  }
  return "The preview could not be loaded. Check the subject and rule fields, then try again.";
}

export function CustomRuleEditor({
  catalog,
  mode,
  rule,
  initialSubjectLabel = "",
}: {
  catalog: CustomRuleCatalog;
  mode: "create" | "edit";
  rule?: CustomRule;
  initialSubjectLabel?: string;
}) {
  const router = useRouter();
  const nameId = useId();
  const descriptionId = useId();
  const metricId = useId();
  const operatorId = useId();
  const thresholdId = useId();
  const sustainId = useId();
  const recoveryId = useId();
  const severityId = useId();
  const enabledId = useId();
  const hoursId = useId();
  const reasonsId = useId();
  const [draft, setDraft] = useState<EditorDraft>(() => rule ? draftFromRule(rule) : emptyDraft(catalog));
  const [subjectLabel, setSubjectLabel] = useState(initialSubjectLabel);
  const [hours, setHours] = useState(Math.min(24, catalog.preview_hours_max));
  const [preview, setPreview] = useState<CustomRulePreview | null>(null);
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [previewError, setPreviewError] = useState("");
  const [saving, setSaving] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [reasons, setReasons] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ currentVersion: string; updatedBy: string | null } | null>(null);
  const [saved, setSaved] = useState(false);

  const metric = selectedMetric(catalog, draft);
  const validation = draftReason(draft, metric);

  useEffect(() => {
    if (validation !== null) {
      setPreview(null);
      setPreviewState("idle");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setPreviewState("loading");
      setPreviewError("");
      api.previewCustomRule(previewBody(draft), hours, controller.signal)
        .then((value) => {
          setPreview(value);
          setPreviewState("ready");
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setPreview(null);
          setPreviewError(previewFailure(error));
          setPreviewState("error");
        });
    }, 450);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [draft, hours, validation]);

  const summary = useMemo(() => {
    if (!metric || draft.threshold.trim() === "" || !Number.isFinite(Number(draft.threshold))) return null;
    return editorSummary(
      draft.operator,
      Number(draft.threshold),
      metric.unit,
      Number(draft.sustain),
      Number(draft.recovery),
    );
  }, [draft.operator, draft.recovery, draft.sustain, draft.threshold, metric]);

  function set<K extends keyof EditorDraft>(key: K, value: EditorDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setSaved(false);
  }

  function changeKind(kind: CustomRuleSubjectKind) {
    const firstMetric = metricsFor(catalog, kind)[0]?.metric ?? "";
    setDraft((current) => ({
      ...current,
      subjectKind: kind,
      logSourceId: "",
      sourceClass: "",
      wafEndpointId: "",
      ipsEntityId: "",
      metric: firstMetric,
    }));
    setSubjectLabel("");
  }

  async function save() {
    if (saving) return;
    setReasons([]);
    setFormError(null);
    setConflict(null);
    setSaved(false);
    if (validation) {
      setFieldError(validation);
      return;
    }
    setFieldError(null);
    setSaving(true);
    try {
      if (mode === "create") {
        const created = await api.createCustomRule(createBody(draft));
        router.push(`/custom-rules/${created.id}`);
        return;
      }
      if (!rule) return;
      await api.updateCustomRule(rule.id, updateBody(draft, rule));
      setSaved(true);
      router.refresh();
    } catch (error) {
      const refusal = classifySaveError(error);
      if (refusal.kind === "reasons") setReasons(refusal.reasons);
      else if (refusal.kind === "cap") {
        setFormError(`${refusal.detail} The configured limit is ${catalog.max_enabled}. Disable another rule before enabling this one.`);
      } else if (refusal.kind === "version") {
        setConflict({ currentVersion: refusal.currentVersion, updatedBy: refusal.updatedBy });
      } else setFormError(refusal.detail);
    } finally {
      setSaving(false);
    }
  }

  const subjectValue = {
    logSourceId: draft.logSourceId,
    sourceClass: draft.sourceClass,
    wafEndpointId: draft.wafEndpointId,
    ipsEntityId: draft.ipsEntityId,
  };

  return (
    <div className="cr-editor-layout">
      <form
        className="cr-editor panel"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="cr-field">
          <label htmlFor={nameId}>Name</label>
          <input id={nameId} className="field" maxLength={80} autoComplete="off" value={draft.name} onChange={(event) => set("name", event.target.value)} />
        </div>
        <div className="cr-field">
          <label htmlFor={descriptionId}>Description</label>
          <textarea id={descriptionId} rows={3} maxLength={2000} value={draft.description} onChange={(event) => set("description", event.target.value)} />
        </div>

        {initialSubjectLabel && <p className="field-hint">Stored subject: {initialSubjectLabel}</p>}
        <SubjectPicker
          catalog={catalog}
          kind={draft.subjectKind}
          value={subjectValue}
          onKind={changeKind}
          onChange={(value) => setDraft((current) => ({ ...current, ...value }))}
          onLabel={setSubjectLabel}
        />

        <div className="cr-field">
          <label htmlFor={metricId}>Metric</label>
          <select id={metricId} className="select-control" value={draft.metric} onChange={(event) => set("metric", event.target.value)}>
            {metricsFor(catalog, draft.subjectKind).map((choice) => (
              <option key={choice.metric} value={choice.metric}>{metricLabel(choice.metric)} — {choice.unit}</option>
            ))}
          </select>
          {metric && <p className="field-hint">{metric.note}</p>}
        </div>

        <div className="cr-paired-fields">
          <div className="cr-field">
            <label htmlFor={operatorId}>Operator</label>
            <select id={operatorId} className="select-control" value={draft.operator} onChange={(event) => set("operator", event.target.value)}>
              {catalog.operators.map((operator) => <option key={operator} value={operator}>{operator}</option>)}
            </select>
          </div>
          <div className="cr-field">
            <label htmlFor={thresholdId}>Threshold ({metric?.unit ?? "unit"})</label>
            <div className="cr-input-unit">
              <input id={thresholdId} className="field num" inputMode="decimal" value={draft.threshold} onChange={(event) => set("threshold", event.target.value)} />
              <span>{metric?.unit ?? "—"}</span>
            </div>
            {metric?.is_ratio && <p className="field-hint">Enter the ratio as a share greater than 0 and less than 1.</p>}
          </div>
        </div>

        <div className="cr-paired-fields">
          <div className="cr-field">
            <label htmlFor={sustainId}>Sustain (buckets)</label>
            <input id={sustainId} className="field num" type="number" min={1} step={1} value={draft.sustain} onChange={(event) => set("sustain", event.target.value)} />
          </div>
          <div className="cr-field">
            <label htmlFor={recoveryId}>Recovery (buckets)</label>
            <input id={recoveryId} className="field num" type="number" min={1} step={1} value={draft.recovery} onChange={(event) => set("recovery", event.target.value)} />
          </div>
        </div>
        {summary && <p className="cr-summary">{summary}</p>}

        <div className="cr-paired-fields">
          <div className="cr-field">
            <label htmlFor={severityId}>Severity</label>
            <select id={severityId} className="select-control" value={draft.severity} onChange={(event) => set("severity", event.target.value)}>
              {SEVERITIES.map((severity) => <option key={severity}>{severity}</option>)}
            </select>
          </div>
          <label className="cr-enabled" htmlFor={enabledId}>
            <input id={enabledId} type="checkbox" checked={draft.enabled} onChange={(event) => set("enabled", event.target.checked)} />
            Enabled
          </label>
        </div>

        {fieldError && <p className="field-error" role="alert">{fieldError}</p>}
        {reasons.length > 0 && (
          <ul id={reasonsId} className="cr-reasons" aria-label="Validation reasons" role="alert">
            {reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
          </ul>
        )}
        {formError && <div className="banner crit" role="alert">{formError}</div>}
        {conflict && (
          <div className="banner warn" role="alert">
            <span>
              This rule was changed by {conflict.updatedBy ?? "another operator"} at {conflict.currentVersion}. Your draft was not written.
              <button type="button" className="action secondary cr-inline-action" onClick={() => router.refresh()}>Reload the current rule</button>
            </span>
          </div>
        )}
        {saved && <p role="status" className="cr-saved">Rule saved.</p>}
        <div className="actions">
          <button className="action" type="submit" disabled={saving}>{saving ? "Saving…" : mode === "create" ? "Create rule" : "Save changes"}</button>
        </div>
      </form>

      <aside className="cr-preview panel" aria-labelledby="preview-title">
        <div className="cr-preview-head">
          <div>
            <h2 id="preview-title">Draft preview</h2>
            <p>Replays this draft over stored aggregates. It persists nothing and sends nothing to QRadar.</p>
          </div>
          <label htmlFor={hoursId}>
            Window
            <span className="cr-input-unit">
              <input
                id={hoursId}
                className="field num"
                type="number"
                min={catalog.preview_hours_min}
                max={catalog.preview_hours_max}
                value={hours}
                onChange={(event) => setHours(Number(event.target.value))}
              />
              <span>hours</span>
            </span>
          </label>
        </div>
        {validation ? (
          <div className="notice"><p>Complete the subject and condition to preview this rule.</p></div>
        ) : previewState === "loading" ? (
          <p className="loading-state" role="status">Loading rule preview…</p>
        ) : previewState === "error" ? (
          <div className="notice" role="alert"><p><strong>Rule preview could not be loaded.</strong></p><p>{previewError}</p></div>
        ) : preview ? (
          <div className="cr-preview-content">
            <PreviewChart preview={preview} subjectLabel={subjectLabel} />
            <PreviewSummary preview={preview} />
          </div>
        ) : null}
      </aside>
    </div>
  );
}
