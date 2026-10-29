"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

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
  reasonTexts,
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
import type { Locale } from "@/i18n/locale";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import { enumLabel } from "@/i18n/enumLabels";

const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

function previewFailure(error: unknown, t: TextTranslator, locale: Locale): string {
  if (error instanceof ApiError && error.status === 422) {
    const detail = error.detail;
    if (typeof detail === "object" && detail !== null && "reasons" in detail) {
      const reasons = (detail as { reasons?: unknown }).reasons;
      if (Array.isArray(reasons)) return reasonTexts(error, t, locale).join(" ");
    }
  }
  return t("customRules.editor.previewFailed");
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
  const t = useTranslations("customRules.editor");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
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
  const validation = draftReason(draft, metric, say);

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
          setPreviewError(previewFailure(error, say, locale));
          setPreviewState("error");
        });
    }, 450);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [draft, hours, locale, say, validation]);

  const summary = useMemo(() => {
    if (!metric || draft.threshold.trim() === "" || !Number.isFinite(Number(draft.threshold))) return null;
    return editorSummary(
      draft.operator,
      Number(draft.threshold),
      metric.unit,
      Number(draft.sustain),
      Number(draft.recovery),
      locale,
      say,
    );
  }, [draft.operator, draft.recovery, draft.sustain, draft.threshold, locale, metric, say]);

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
      pwValueId: "",
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
      const refusal = classifySaveError(error, say, locale);
      if (refusal.kind === "reasons") setReasons(refusal.reasons);
      else if (refusal.kind === "cap") {
        setFormError(t("cap", { detail: refusal.detail, limit: catalog.max_enabled }));
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
    pwValueId: draft.pwValueId,
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
          <label htmlFor={nameId}>{t("name")}</label>
          <input id={nameId} className="field" maxLength={80} autoComplete="off" value={draft.name} onChange={(event) => set("name", event.target.value)} />
        </div>
        <div className="cr-field">
          <label htmlFor={descriptionId}>{t("description")}</label>
          <textarea id={descriptionId} rows={3} maxLength={2000} value={draft.description} onChange={(event) => set("description", event.target.value)} />
        </div>

        {initialSubjectLabel && <p className="field-hint">{t("storedSubject", { subject: initialSubjectLabel })}</p>}
        <SubjectPicker
          catalog={catalog}
          kind={draft.subjectKind}
          value={subjectValue}
          onKind={changeKind}
          onChange={(value) => setDraft((current) => ({ ...current, ...value }))}
          onLabel={setSubjectLabel}
        />

        <div className="cr-field">
          <label htmlFor={metricId}>{t("metric")}</label>
          <select id={metricId} className="select-control" value={draft.metric} onChange={(event) => set("metric", event.target.value)}>
            {metricsFor(catalog, draft.subjectKind).map((choice) => (
              <option key={choice.metric} value={choice.metric}>{metricLabel(choice.metric, say)} — {choice.unit}</option>
            ))}
          </select>
          {metric && <p className="field-hint">{metric.note}</p>}
        </div>

        <div className="cr-paired-fields">
          <div className="cr-field">
            <label htmlFor={operatorId}>{t("operator")}</label>
            <select id={operatorId} className="select-control" value={draft.operator} onChange={(event) => set("operator", event.target.value)}>
              {catalog.operators.map((operator) => <option key={operator} value={operator}>{operator}</option>)}
            </select>
          </div>
          <div className="cr-field">
            <label htmlFor={thresholdId}>{t("threshold", { unit: metric?.unit ?? t("unit") })}</label>
            <div className="cr-input-unit">
              <input id={thresholdId} className="field num" inputMode="decimal" value={draft.threshold} onChange={(event) => set("threshold", event.target.value)} />
              <span>{metric?.unit ?? "—"}</span>
            </div>
            {metric?.is_ratio && <p className="field-hint">{t("ratioHint")}</p>}
          </div>
        </div>

        <div className="cr-paired-fields">
          <div className="cr-field">
            <label htmlFor={sustainId}>{t("sustain")}</label>
            <input id={sustainId} className="field num" type="number" min={1} step={1} value={draft.sustain} onChange={(event) => set("sustain", event.target.value)} />
          </div>
          <div className="cr-field">
            <label htmlFor={recoveryId}>{t("recovery")}</label>
            <input id={recoveryId} className="field num" type="number" min={1} step={1} value={draft.recovery} onChange={(event) => set("recovery", event.target.value)} />
          </div>
        </div>
        {summary && <p className="cr-summary">{summary}</p>}

        <div className="cr-paired-fields">
          <div className="cr-field">
            <label htmlFor={severityId}>{t("severity")}</label>
            <select id={severityId} className="select-control" value={draft.severity} onChange={(event) => set("severity", event.target.value)}>
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {enumLabel(say, "severityPill", severity)}
                </option>
              ))}
            </select>
          </div>
          <label className="cr-enabled" htmlFor={enabledId}>
            <input id={enabledId} type="checkbox" checked={draft.enabled} onChange={(event) => set("enabled", event.target.checked)} />
            {t("enabled")}
          </label>
        </div>

        {fieldError && <p className="field-error" role="alert">{fieldError}</p>}
        {reasons.length > 0 && (
          <ul id={reasonsId} className="cr-reasons" aria-label={t("validationReasons")} role="alert">
            {reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
          </ul>
        )}
        {formError && <div className="banner crit" role="alert">{formError}</div>}
        {conflict && (
          <div className="banner warn" role="alert">
            <span>
              {t("conflict", {
                operator: conflict.updatedBy ?? t("anotherOperator"),
                version: conflict.currentVersion,
              })}
              <button type="button" className="action secondary cr-inline-action" onClick={() => router.refresh()}>{t("reload")}</button>
            </span>
          </div>
        )}
        {saved && <p role="status" className="cr-saved">{t("saved")}</p>}
        <div className="actions">
          <button className="action" type="submit" disabled={saving}>{saving ? t("saving") : mode === "create" ? t("create") : t("save")}</button>
        </div>
      </form>

      <aside className="cr-preview panel" aria-labelledby="preview-title">
        <div className="cr-preview-head">
          <div>
            <h2 id="preview-title">{t("previewTitle")}</h2>
            <p>{t("previewDescription")}</p>
          </div>
          <label htmlFor={hoursId}>
            {t("window")}
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
              <span>{t("hours")}</span>
            </span>
          </label>
        </div>
        {validation ? (
          <div className="notice"><p>{t("complete")}</p></div>
        ) : previewState === "loading" ? (
          <p className="loading-state" role="status">{t("loading")}</p>
        ) : previewState === "error" ? (
          <div className="notice" role="alert"><p><strong>{t("errorTitle")}</strong></p><p>{previewError}</p></div>
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
