"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { asTextTranslator } from "@/i18n/textTranslator";

import { NAME_MAX, nameProblem } from "./DashboardMenu";

import "./dashboard.css";

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="dg-modal"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2 id={titleId} className="dg-modal-title">
        {title}
      </h2>
      {children}
    </dialog>
  );
}

function DashboardNameField({
  value,
  label,
  error,
  onChange,
}: {
  value: string;
  label: string;
  error: string | null;
  onChange: (value: string) => void;
}) {
  const t = useTranslations("widgets.menu");
  const fieldId = useId();
  const errorId = useId();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  return (
    <div className="dg-modal-field">
      <label htmlFor={fieldId}>{label}</label>
      <input
        ref={input}
        id={fieldId}
        value={value}
        maxLength={NAME_MAX + 20}
        autoComplete="off"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      <p className="dg-modal-hint" id={error ? errorId : undefined}>
        {t("nameHint", { max: NAME_MAX })}
      </p>
    </div>
  );
}

export function NameTakenDialog({
  initialName,
  serverMessage,
  busy,
  onSave,
  onClose,
}: {
  initialName: string;
  serverMessage: string;
  busy: boolean;
  onSave: (name: string) => Promise<string | null>;
  onClose: () => void;
}) {
  const t = useTranslations("widgets.saveDialogs");
  const common = useTranslations("common");
  const root = asTextTranslator(useTranslations());
  const [name, setName] = useState(initialName);
  const [error, setError] = useState<string | null>(serverMessage);
  const submit = async () => {
    const problem = nameProblem(name, root);
    if (problem) return setError(problem);
    const refusal = await onSave(name.trim());
    if (refusal) setError(refusal);
  };
  return (
    <Modal title={t("nameTakenTitle")} onClose={onClose}>
      <form
        className="dg-modal-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <p className="dg-modal-error" role="alert">
          {error}
        </p>
        <DashboardNameField value={name} label={t("name")} error={error} onChange={setName} />
        <div className="dg-modal-actions">
          <button type="button" className="action secondary" onClick={onClose}>
            {t("cancel")}
          </button>
          <button type="submit" className="action" disabled={busy}>
            {busy ? common("saving") : t("saveDashboard")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function ConflictDialog({
  dashboardName,
  busy,
  onReload,
  onSaveCopy,
  onClose,
}: {
  dashboardName: string;
  busy: boolean;
  onReload: () => Promise<void>;
  onSaveCopy: (name: string) => Promise<string | null>;
  onClose: () => void;
}) {
  const t = useTranslations("widgets.saveDialogs");
  const common = useTranslations("common");
  const root = asTextTranslator(useTranslations());
  const [name, setName] = useState(t("copyName", { name: dashboardName }));
  const [error, setError] = useState<string | null>(null);
  const submitCopy = async () => {
    const problem = nameProblem(name, root);
    if (problem) return setError(problem);
    const refusal = await onSaveCopy(name.trim());
    if (refusal) setError(refusal);
  };
  return (
    <Modal title={t("conflictTitle")} onClose={onClose}>
      <div className="dg-modal-form">
        <ul className="dg-modal-choices">
          <li>
            {t.rich("conflictReload", { b: (chunks) => <strong>{chunks}</strong> })}
          </li>
          <li>
            {t.rich("conflictCopy", { b: (chunks) => <strong>{chunks}</strong> })}
          </li>
          <li>
            {t.rich("conflictKeep", { b: (chunks) => <strong>{chunks}</strong> })}
          </li>
        </ul>
        <DashboardNameField value={name} label={t("copyLabel")} error={error} onChange={setName} />
        {error ? (
          <p className="dg-modal-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="dg-modal-actions">
          <button type="button" className="action secondary" onClick={onClose}>
            {t("keepEditing")}
          </button>
          <button type="button" className="action secondary" disabled={busy} onClick={() => void onReload()}>
            {t("reload")}
          </button>
          <button type="button" className="action" disabled={busy} onClick={() => void submitCopy()}>
            {busy ? common("saving") : t("saveCopy")}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function UnsavedChangesDialog({
  dashboardName,
  onDiscard,
  onKeep,
}: {
  dashboardName: string;
  onDiscard: () => void;
  onKeep: () => void;
}) {
  const t = useTranslations("widgets.saveDialogs");
  const keep = useRef<HTMLButtonElement>(null);
  useEffect(() => keep.current?.focus(), []);
  return (
    <Modal title={t("discardTitle", { name: dashboardName })} onClose={onKeep}>
      <div className="dg-modal-form">
        <ul className="dg-modal-choices">
          <li>{t.rich("discardChoice", { b: (chunks) => <strong>{chunks}</strong> })}</li>
          <li>{t.rich("keepChoice", { b: (chunks) => <strong>{chunks}</strong> })}</li>
        </ul>
        <div className="dg-modal-actions">
          <button ref={keep} type="button" className="action secondary" onClick={onKeep}>
            {t("keepEditing")}
          </button>
          <button type="button" className="action" onClick={onDiscard}>
            {t("discard")}
          </button>
        </div>
      </div>
    </Modal>
  );
}
