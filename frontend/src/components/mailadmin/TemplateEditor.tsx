"use client";

// One template's editor: subject, HTML body, optional text body and the switch, with the
// placeholders beside them. Save stores the sanitised HTML and the editor then shows exactly that,
// and says so when the sanitiser changed what was typed. Reset deletes the stored row after a
// confirmation that names what the key sends from then on.

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, type FormEvent } from "react";

import { UtcTime } from "@/components/ui/UtcTime";
import type { EmailPlaceholders, EmailTemplateList } from "@/lib/api";

import { insertAt, placeholderToken, sourceOf } from "./mail";
import { PartError } from "./PartError";
import { isDirty, type Editor, type TemplateWorkspace } from "./useTemplateWorkspace";

type Field = "subject" | "html" | "text";

export function TemplateEditor({
  keyName,
  editor,
  list,
  workspace,
  onInsertReady,
}: {
  keyName: string;
  editor: Editor | undefined;
  list: EmailTemplateList;
  workspace: TemplateWorkspace;
  /** Hands the page the function that inserts a placeholder at the cursor of the last field used. */
  onInsertReady: (insert: ((name: string) => void) | null) => void;
}) {
  const t = useTranslations("mailAdmin.editor");
  const row = list.templates.find((item) => item.key === keyName);
  const label = row?.label ?? keyName;
  const headingId = useId();

  if (!editor || editor.load === "loading") {
    return (
      <section className="mail-section" aria-labelledby={headingId}>
        <h2 className="mail-h" id={headingId}>
          {t("heading", { label })}
        </h2>
        <p className="notice" role="status" aria-live="polite">
          {t("loading", { label })}
        </p>
      </section>
    );
  }
  if (editor.load === "error" || editor.load === "forbidden" || !editor.draft || !editor.baseline) {
    return (
      <section className="mail-section" aria-labelledby={headingId}>
        <h2 className="mail-h" id={headingId}>
          {t("heading", { label })}
        </h2>
        <PartError
          what={t("what", { label })}
          httpStatus={editor.load === "forbidden" ? 403 : editor.httpStatus}
          retryAfter={null}
          onRetry={() => void workspace.reopen(keyName)}
        />
      </section>
    );
  }
  return (
    <Form
      keyName={keyName}
      editor={editor}
      list={list}
      workspace={workspace}
      onInsertReady={onInsertReady}
      label={label}
      headingId={headingId}
    />
  );
}

function Form({
  keyName,
  editor,
  list,
  workspace,
  onInsertReady,
  label,
  headingId,
}: {
  keyName: string;
  editor: Editor;
  list: EmailTemplateList;
  workspace: TemplateWorkspace;
  onInsertReady: (insert: ((name: string) => void) | null) => void;
  label: string;
  headingId: string;
}) {
  const t = useTranslations("mailAdmin.editor");
  const ids = { subject: useId(), html: useId(), text: useId(), enabled: useId() };
  const refs = {
    subject: useRef<HTMLInputElement>(null),
    html: useRef<HTMLTextAreaElement>(null),
    text: useRef<HTMLTextAreaElement>(null),
  };
  const lastField = useRef<Field>("html");
  const pendingCaret = useRef<{ field: Field; caret: number } | null>(null);

  const draft = editor.draft!;
  const baseline = editor.baseline!;
  const dirty = isDirty(editor);
  const isDefault = keyName === "default";
  const defaultRow = list.templates.find((item) => item.alert_type === null);
  const defaultStored = !!defaultRow && defaultRow.customised && defaultRow.enabled;
  // What the key sends once its stored row is gone.
  const fallback = isDefault || !defaultStored ? "shipped" : "default";
  const summary = list.templates.find((item) => item.key === keyName);
  const source = summary ? sourceOf(summary, list) : baseline.effective_source;

  // A placeholder inserted at the cursor moves the caret behind it once the new value is in the
  // field; setting the selection before React writes the value would lose it.
  useEffect(() => {
    const pending = pendingCaret.current;
    if (!pending) return;
    pendingCaret.current = null;
    const element = refs[pending.field].current;
    if (element) {
      element.focus();
      element.setSelectionRange(pending.caret, pending.caret);
    }
  });

  function insert(name: string) {
    const field = lastField.current;
    const element = refs[field].current;
    const value = draft[field];
    const start = element?.selectionStart ?? value.length;
    const end = element?.selectionEnd ?? value.length;
    const next = insertAt(value, start, end, placeholderToken(name));
    pendingCaret.current = { field, caret: next.caret };
    workspace.edit(keyName, { [field]: next.value });
  }

  // The placeholder list sits beside the preview, away from this form; it inserts through here.
  const insertRef = useRef(insert);
  insertRef.current = insert;
  useEffect(() => {
    onInsertReady((name) => insertRef.current(name));
    return () => onInsertReady(null);
  }, [onInsertReady]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void workspace.save(keyName);
  }

  const stateCase = isDefault
    ? baseline.customised
      ? baseline.enabled
        ? "defaultOwn"
        : "defaultOff"
      : "defaultNone"
    : baseline.customised
      ? baseline.enabled
        ? "typeOwn"
        : "typeOff"
      : "typeFallback";

  return (
    <section className="mail-section" aria-labelledby={headingId}>
      <div className="mail-head">
        <h2 className="mail-h" id={headingId}>
          {t("heading", { label })}
        </h2>
        {dirty ? <span className="pill warn">{t("unsaved")}</span> : null}
      </div>
      <p className="mail-hint">
        {t(`state.${stateCase}` as "state.typeOwn", {
          label,
          fallback: t(`fallback.${source === "default" ? "default" : "shipped"}` as "fallback.shipped"),
        })}{" "}
        {baseline.customised && baseline.updated_at ? (
          <>
            {t.rich("saved", {
              time: () => <UtcTime value={baseline.updated_at} />,
              by: baseline.updated_by ?? "",
              hasBy: baseline.updated_by ? "yes" : "no",
            })}
          </>
        ) : null}
      </p>

      <form className="mail-form" onSubmit={onSubmit} aria-label={t("formLabel", { label })}>
        <div className="mail-field">
          <label htmlFor={ids.subject}>{t("subject")}</label>
          <input
            id={ids.subject}
            ref={refs.subject}
            value={draft.subject}
            spellCheck={false}
            autoComplete="off"
            onFocus={() => (lastField.current = "subject")}
            onChange={(event) => workspace.edit(keyName, { subject: event.target.value })}
          />
        </div>
        <div className="mail-field">
          <label htmlFor={ids.html}>{t("html")}</label>
          <textarea
            id={ids.html}
            ref={refs.html}
            className="mail-code"
            rows={14}
            value={draft.html}
            spellCheck={false}
            wrap="off"
            aria-describedby={`${ids.html}-hint`}
            onFocus={() => (lastField.current = "html")}
            onChange={(event) => workspace.edit(keyName, { html: event.target.value })}
          />
          <p className="mail-hint" id={`${ids.html}-hint`}>
            {t("htmlHint")}
          </p>
        </div>
        <div className="mail-field">
          <label htmlFor={ids.text}>{t("text")}</label>
          <textarea
            id={ids.text}
            ref={refs.text}
            className="mail-code"
            rows={4}
            value={draft.text}
            spellCheck={false}
            wrap="off"
            aria-describedby={`${ids.text}-hint`}
            onFocus={() => (lastField.current = "text")}
            onChange={(event) => workspace.edit(keyName, { text: event.target.value })}
          />
          <p className="mail-hint" id={`${ids.text}-hint`}>
            {t("textHint")}
          </p>
        </div>
        <div className="mail-check">
          <input
            id={ids.enabled}
            type="checkbox"
            checked={draft.enabled}
            aria-describedby={`${ids.enabled}-hint`}
            onChange={(event) => workspace.edit(keyName, { enabled: event.target.checked })}
          />
          <div>
            <label htmlFor={ids.enabled}>{t("enabled")}</label>
            <p className="mail-hint" id={`${ids.enabled}-hint`}>
              {t("enabledHint", {
                fallback: t(`fallback.${fallback}` as "fallback.shipped"),
              })}
            </p>
          </div>
        </div>

        <div className="actions">
          <button type="submit" className="action" disabled={!dirty || editor.saving}>
            {editor.saving ? t("saving") : t("save")}
          </button>
          {dirty ? (
            <button
              type="button"
              className="action secondary"
              disabled={editor.saving}
              onClick={() => workspace.discard(keyName)}
            >
              {t("discard")}
            </button>
          ) : null}
          {baseline.customised && editor.reset === "idle" ? (
            <button
              type="button"
              className="action secondary"
              disabled={editor.saving}
              onClick={() => workspace.askReset(keyName)}
            >
              {isDefault ? t("resetDefault") : t("resetType")}
            </button>
          ) : null}
        </div>

        {editor.save?.kind === "saved" ? (
          <div className={editor.save.sanitiserChanged ? "banner warn" : "banner ok"} role="status">
            <div>
              <p>
                <strong>{t("savedHeading")}</strong>
              </p>
              <p>{editor.save.sanitiserChanged ? t("sanitiserChanged") : t("sanitiserKept")}</p>
            </div>
          </div>
        ) : null}
        {editor.save?.kind === "failed" ? (
          <div className="banner warn" role="alert">
            <div>
              <p>
                <strong>{t("notSaved")}</strong>
              </p>
              <ul className="mail-lines">
                {editor.save.lines.map((line, index) => (
                  <li key={`${index}-${line}`}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        {editor.reset !== "idle" ? (
          <div className="banner warn" role="alert">
            <div>
              <p>
                <strong>{t("resetHeading", { label })}</strong>
              </p>
              <p>
                {isDefault
                  ? t("resetDefaultBody")
                  : t(`resetTypeBody.${fallback}` as "resetTypeBody.shipped", { label })}
                {dirty ? ` ${t("resetLosesDraft")}` : null}
              </p>
              {editor.resetLines.length > 0 ? (
                <ul className="mail-lines">
                  {editor.resetLines.map((line, index) => (
                    <li key={`${index}-${line}`}>{line}</li>
                  ))}
                </ul>
              ) : null}
              <div className="actions">
                <button
                  type="button"
                  className="action"
                  disabled={editor.reset === "working"}
                  onClick={() => void workspace.reset(keyName)}
                >
                  {editor.reset === "working" ? t("resetting") : t("resetConfirm")}
                </button>
                <button
                  type="button"
                  className="action secondary"
                  disabled={editor.reset === "working"}
                  onClick={() => workspace.cancelReset(keyName)}
                >
                  {t("resetKeep")}
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </form>
    </section>
  );
}

export function PlaceholderList({
  placeholders,
  onInsert,
}: {
  placeholders: EmailPlaceholders | null;
  onInsert: (name: string) => void;
}) {
  const t = useTranslations("mailAdmin.placeholders");
  const headingId = useId();
  return (
    <div className="mail-placeholders" role="group" aria-labelledby={headingId}>
      <h3 className="mail-h3" id={headingId}>
        {t("heading")}
      </h3>
      {placeholders === null ? (
        <p className="mail-hint">{t("unavailable")}</p>
      ) : (
        <>
          <p className="mail-hint">{t("help", { syntax: placeholders.syntax })}</p>
          <ul className="mail-placeholder-list">
            {placeholders.placeholders.map((item) => (
              <li key={item.name}>
                <button
                  type="button"
                  className="mail-insert"
                  aria-label={t("insert", { name: placeholderToken(item.name) })}
                  onClick={() => onInsert(item.name)}
                >
                  <code>{placeholderToken(item.name)}</code>
                </button>
                <span className="cell-sub">{item.description}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
