"use client";

// What the e-mail will look like. The returned HTML is rendered in a frame with an empty sandbox:
// no scripts, no same-origin access, no forms, no top navigation, no popups. The sample alert is
// fixed text of the chosen type, never a real alert. A preview built from older text than the
// editor holds says so.

import { useTranslations } from "next-intl";
import { useId, useState } from "react";

import { Tabs } from "@/components/ui/Tabs";
import type { EmailAlertType } from "@/lib/api";

import { ScaledFrame } from "./ScaledFrame";
import { previewIsStale, type Editor, type PreviewReady, type TemplateWorkspace } from "./useTemplateWorkspace";

export function PreviewPane({
  keyName,
  editor,
  alertTypes,
  workspace,
}: {
  keyName: string;
  editor: Editor;
  alertTypes: readonly EmailAlertType[];
  workspace: TemplateWorkspace;
}) {
  const t = useTranslations("mailAdmin.preview");
  const headingId = useId();
  const sampleId = useId();
  const view = editor.preview;
  const shown: PreviewReady | null =
    view.kind === "ok" ? view : view.kind === "loading" ? view.previous : null;
  const stale = previewIsStale(editor);
  const [actual, setActual] = useState(false);

  return (
    <section className="mail-section" aria-labelledby={headingId}>
      <h2 className="mail-h" id={headingId}>
        {t("heading")}
      </h2>
      <div className="mail-preview-controls">
        <div className="mail-field mail-field-inline">
          <label htmlFor={sampleId}>{t("sample")}</label>
          <select
            id={sampleId}
            className="select-control"
            value={editor.sample ?? ""}
            onChange={(event) => workspace.setSample(keyName, event.target.value || null)}
          >
            <option value="">{t("sampleGeneric")}</option>
            {alertTypes.map((type) => (
              <option key={type.key} value={type.key}>
                {type.label}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="action secondary"
          disabled={view.kind === "loading" || editor.draft === null}
          onClick={() => void workspace.refreshPreview(keyName)}
        >
          {view.kind === "loading" ? t("updating") : t("update")}
        </button>
      </div>
      <p className="mail-hint">{t("sampleNote")}</p>

      {view.kind === "failed" ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("failed")}</strong>
            </p>
            <ul className="mail-lines">
              {view.lines.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
            <p>{t("failedAdvice")}</p>
          </div>
        </div>
      ) : null}

      {view.kind === "loading" && shown === null ? (
        <p className="notice" role="status" aria-live="polite">
          {t("building")}
        </p>
      ) : null}

      {shown !== null && view.kind !== "failed" ? (
        <>
          {stale ? (
            <p className="mail-stale" role="status">
              <span className="pill warn">{t("outOfDate")}</span> {t("outOfDateNote")}
            </p>
          ) : null}
          <p className="mail-subject">
            <span className="mail-subject-label">{t("subject")}</span>
            <span className="mail-mono">{shown.data.subject}</span>
          </p>
          <Tabs
            label={t("viewLabel")}
            tabs={[
              {
                id: "html",
                label: t("viewHtml"),
                content: () => (
                  <ScaledFrame html={shown.data.html} actual={actual} onActual={setActual} />
                ),
              },
              {
                id: "text",
                label: t("viewText"),
                content: () => (
                  <pre className="mail-text" aria-label={t("textLabel")}>
                    {shown.data.text}
                  </pre>
                ),
              },
            ]}
          />
        </>
      ) : null}
    </section>
  );
}
