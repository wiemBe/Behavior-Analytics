"use client";

// The menu for the dashboard shown (UR.5.3, UR.5.5): new from a template, rename, duplicate,
// delete, set or clear the default, share or make private. What it offers is decided by
// `dashboardAccess` from the caller's permissions and ownership, and fails closed: while
// `/me` is unread or failed, no write control exists. The server stays the authority, and a
// `403` it still sends hides the controls and says why.
//
// Input is taken in native `<dialog>` forms. A refused write keeps its dialog open with the
// server's own `detail`; the page re-reads the list after every write, nothing is optimistic.

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";
import {
  createDashboard,
  deleteDashboard,
  describeRefusal,
  duplicateDashboard,
  patchDashboard,
  setDefaultDashboard,
  type DashboardVisibility,
} from "@/lib/dashboardApi";

import { usePopover, type DashboardTarget } from "./DashboardSwitcher";
import { TEMPLATES, templateByKey } from "./templates";
import type { DashboardLayout } from "./types";

import "./dashboard.css";

export const PERM_WRITE = "dashboard:write";
export const PERM_SHARE = "admin:dashboards";
export const NAME_MAX = 80;

export type MenuAction = "rename" | "duplicate" | "default" | "visibility" | "new" | "delete";

/** The dashboard the page shows: the template, or a saved one. */
export type CurrentDashboard =
  | { kind: "template" }
  | {
      kind: "saved";
      id: string;
      name: string;
      ownerSubject: string;
      visibility: DashboardVisibility;
      isDefault: boolean;
      templateKey?: string | null;
    };

/** What `GET /me` said. `loading` and `failed` both grant nothing. */
export type Caller =
  | { status: "loading" }
  | { status: "ok"; subject: string; permissions: ReadonlySet<string> }
  | { status: "failed"; reason: string };

export interface DashboardAccess {
  /** The menu entries to render, in order. */
  actions: readonly MenuAction[];
  /** The one quiet line that says why there is less, or null. */
  note: string | null;
}

/**
 * What the caller may do with the dashboard shown. Pure. `refused` is the `detail` of a `403`
 * the server sent for a write; once set, no write control is offered.
 */
export function dashboardAccess(
  current: CurrentDashboard,
  caller: Caller,
  refused: string | null = null,
): DashboardAccess {
  if (caller.status === "loading") return { actions: [], note: null };
  if (caller.status === "failed") {
    return {
      actions: [],
      note: `Your permissions could not be read (${caller.reason}), so dashboards here are read-only.`,
    };
  }
  if (!caller.permissions.has(PERM_WRITE)) {
    return {
      actions: [],
      note: `Your role cannot save dashboards; it needs the ${PERM_WRITE} permission.`,
    };
  }
  if (refused !== null) {
    return {
      actions: [],
      note: `The server refused the change: ${refused} The controls stay hidden until you reload.`,
    };
  }
  if (current.kind === "template") return { actions: ["new"], note: null };
  if (current.ownerSubject !== caller.subject) {
    return {
      actions: ["duplicate"],
      note: `Shared by ${current.ownerSubject}; you can read it and duplicate it into your own dashboards.`,
    };
  }
  const actions: MenuAction[] = ["rename", "duplicate", "default"];
  if (caller.permissions.has(PERM_SHARE)) actions.push("visibility");
  actions.push("new", "delete");
  return { actions, note: null };
}

/** What happened, for the page: re-read the list, then maybe go somewhere. */
export interface DashboardChange {
  /** A saved dashboard to open, `"start"` for the user's default (or the template), or none. */
  go: DashboardTarget | "start" | null;
}

type DialogState =
  | { kind: "new" }
  | { kind: "rename" }
  | { kind: "delete" }
  | { kind: "failed"; title: string; text: string };

/** A trimmed name of 1 to 80 characters, or the sentence that says what is wrong. */
export function nameProblem(raw: string): string | null {
  const name = raw.trim();
  if (name.length === 0) return "Enter a name.";
  if (name.length > NAME_MAX) return `A name is at most ${NAME_MAX} characters.`;
  return null;
}

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
        // Esc: the dialog closes by unmounting, so the menu owns the focus return.
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

function NameDialog({
  title,
  submit,
  initial,
  withTemplate,
  onSubmit,
  onClose,
}: {
  title: string;
  submit: string;
  initial: string;
  withTemplate: boolean;
  onSubmit: (name: string, templateKey: string) => Promise<string | null>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial);
  const [templateKey, setTemplateKey] = useState(TEMPLATES[0].key);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const fieldId = useId();
  const errorId = useId();
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const send = async () => {
    const problem = nameProblem(name);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    const refusal = await onSubmit(name.trim(), templateKey);
    // A success unmounts the dialog; only a refusal is shown, with the dialog kept open.
    setBusy(false);
    if (refusal) setError(refusal);
  };

  return (
    <Modal title={title} onClose={onClose}>
      <form
        className="dg-modal-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        {withTemplate ? (
          <div className="dg-modal-field">
            <label htmlFor={`${fieldId}-tpl`}>Template</label>
            <select
              id={`${fieldId}-tpl`}
              value={templateKey}
              onChange={(event) => setTemplateKey(event.target.value)}
            >
              {TEMPLATES.map((template) => (
                <option key={template.key} value={template.key}>
                  {template.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="dg-modal-field">
          <label htmlFor={fieldId}>Name</label>
          <input
            id={fieldId}
            ref={inputRef}
            type="text"
            value={name}
            maxLength={NAME_MAX + 20}
            autoComplete="off"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => setName(event.target.value)}
          />
          <p className="dg-modal-hint">1 to {NAME_MAX} characters, unique among your dashboards.</p>
        </div>
        {error ? (
          <p className="dg-modal-error" id={errorId} role="alert">
            {error}
          </p>
        ) : null}
        <div className="dg-modal-actions">
          <button type="button" className="action secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="action" disabled={busy}>
            {busy ? "Saving…" : submit}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ConfirmDelete({
  name,
  isDefault,
  onConfirm,
  onClose,
}: {
  name: string;
  isDefault: boolean;
  onConfirm: () => Promise<string | null>;
  onClose: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => cancelRef.current?.focus(), []);
  return (
    <Modal title={`Delete "${name}"?`} onClose={onClose}>
      <div className="dg-modal-form">
        <p className="dg-modal-text">
          The dashboard and its layout are removed for good, and it disappears for anyone it was
          shared with.
          {isDefault ? " It is your default; afterwards the start page shows the SOC Overview template." : ""}
        </p>
        {error ? (
          <p className="dg-modal-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="dg-modal-actions">
          <button type="button" ref={cancelRef} className="action secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="action dg-danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              const refusal = await onConfirm();
              setBusy(false);
              if (refusal) setError(refusal);
            }}
          >
            {busy ? "Deleting…" : "Delete dashboard"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export interface DashboardMenuProps {
  current: CurrentDashboard;
  access: DashboardAccess;
  onChanged: (change: DashboardChange) => void;
  /** The server answered 403 to a write: the page hides the controls and says why. */
  onRefused: (detail: string) => void;
  /** Customize-only restore/reset changes the in-memory layout; Done performs the write. */
  customizing?: boolean;
  onResetLayout?: (layout: DashboardLayout) => void;
  /** New, duplicate and delete navigate; the page may interpose its unsaved-changes guard. */
  onRequestNavigation?: (action: () => void) => void;
}

export function DashboardMenu({
  current,
  access,
  onChanged,
  onRefused,
  customizing = false,
  onResetLayout,
  onRequestNavigation,
}: DashboardMenuProps) {
  const { open, setOpen, root, trigger } = usePopover();
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [busy, setBusy] = useState(false);
  const panelId = useId();

  if (access.actions.length === 0) return null;
  const saved = current.kind === "saved" ? current : null;
  const offers = (action: MenuAction) => access.actions.includes(action);
  const restoreTemplate = saved ? templateByKey(saved.templateKey ?? null) : undefined;
  const unknownTemplate = saved?.templateKey && !restoreTemplate ? saved.templateKey : null;
  const navigate = (action: () => void) => {
    setOpen(false);
    trigger.current?.focus();
    if (onRequestNavigation) onRequestNavigation(action);
    else action();
  };

  const close = () => {
    setDialog(null);
    // After the dialog has unmounted, so the browser's own focus restoration cannot win.
    queueMicrotask(() => trigger.current?.focus());
  };

  /** Runs a write. Returns the sentence to show when it was refused, null when it went through. */
  const run = async (write: () => Promise<DashboardChange>): Promise<string | null> => {
    try {
      const change = await write();
      setDialog(null);
      onChanged(change);
      queueMicrotask(() => trigger.current?.focus());
      return null;
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setDialog(null);
        onRefused(describeRefusal(error));
        return null;
      }
      return describeRefusal(error);
    }
  };

  /** A write with no form of its own: a refusal opens a dialog that says what the server said. */
  const direct = async (title: string, write: () => Promise<DashboardChange>) => {
    setOpen(false);
    setBusy(true);
    const refusal = await run(write);
    setBusy(false);
    if (refusal) setDialog({ kind: "failed", title, text: refusal });
  };

  const choose = (action: MenuAction) => {
    if (action === "new" || action === "rename" || action === "delete") {
      const show = () => setDialog({ kind: action });
      if (action === "new" || action === "delete") navigate(show);
      else {
        setOpen(false);
        show();
      }
      return;
    }
    if (!saved) return;
    if (action === "duplicate") {
      navigate(() => {
        void direct(`"${saved.name}" could not be duplicated`, async () => {
          const copy = await duplicateDashboard(saved.id);
          return { go: { kind: "saved", id: copy.id } };
        });
      });
    } else if (action === "default") {
      void direct(
        saved.isDefault ? "The default could not be cleared" : `"${saved.name}" could not be made your default`,
        async () => {
          await setDefaultDashboard(saved.isDefault ? null : saved.id);
          return { go: null };
        },
      );
    } else {
      const shared = saved.visibility === "SHARED";
      void direct(
        shared
          ? `"${saved.name}" could not be made private`
          : `"${saved.name}" could not be shared`,
        async () => {
          await patchDashboard(saved.id, { visibility: shared ? "PRIVATE" : "SHARED" });
          return { go: null };
        },
      );
    }
  };

  const label = (action: MenuAction): string => {
    switch (action) {
      case "rename":
        return "Rename…";
      case "duplicate":
        return saved && !access.actions.includes("rename")
          ? "Duplicate into my dashboards"
          : "Duplicate";
      case "default":
        return saved?.isDefault ? "Clear default" : "Set as default";
      case "visibility":
        return saved?.visibility === "SHARED" ? "Make private" : "Share with everyone";
      case "new":
        return "New dashboard from template…";
      case "delete":
        return "Delete…";
    }
  };

  return (
    <div className="dg-pop" ref={root}>
      <button
        type="button"
        ref={trigger}
        className="dg-trigger"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={busy}
        onClick={() => setOpen(!open)}
      >
        Manage
      </button>
      {open ? (
        <div className="dg-pop-panel dg-menu-panel" id={panelId}>
          <ul className="dg-pop-list" aria-label="Dashboard actions">
            {access.actions.map((action) => (
              <li key={action}>
                <button
                  type="button"
                  className={`dg-pop-item dg-menu-item${action === "delete" ? " dg-menu-danger" : ""}`}
                  onClick={() => choose(action)}
                >
                  {label(action)}
                </button>
              </li>
            ))}
            {customizing && saved && offers("rename") ? (
              <li>
                {restoreTemplate ? (
                  <button
                    type="button"
                    className="dg-pop-item dg-menu-item"
                    onClick={() => {
                      setOpen(false);
                      setDialog({ kind: "failed", title: "restore", text: "" });
                    }}
                  >
                    Restore defaults…
                  </button>
                ) : saved.templateKey === null || saved.templateKey === undefined ? (
                  <button
                    type="button"
                    className="dg-pop-item dg-menu-item"
                    onClick={() => {
                      setOpen(false);
                      setDialog({ kind: "failed", title: "reset", text: "" });
                    }}
                  >
                    Reset to empty…
                  </button>
                ) : null}
              </li>
            ) : null}
          </ul>
          {customizing && unknownTemplate ? (
            <p className="dg-pop-note">
              Defaults cannot be restored because this client does not know template {unknownTemplate}.
            </p>
          ) : null}
        </div>
      ) : null}
      {dialog?.kind === "new" ? (
        <NameDialog
          title="New dashboard from template"
          submit="Create dashboard"
          initial=""
          withTemplate
          onClose={close}
          onSubmit={(name, key) =>
            run(async () => {
              const template = TEMPLATES.find((t) => t.key === key) ?? TEMPLATES[0];
              const made = await createDashboard({
                name,
                layout: template.layout,
                template_key: template.key,
                template_version: template.version,
              });
              return { go: { kind: "saved", id: made.id } };
            })
          }
        />
      ) : null}
      {dialog?.kind === "rename" && saved ? (
        <NameDialog
          title="Rename dashboard"
          submit="Rename"
          initial={saved.name}
          withTemplate={false}
          onClose={close}
          onSubmit={(name) =>
            run(async () => {
              await patchDashboard(saved.id, { name });
              return { go: null };
            })
          }
        />
      ) : null}
      {dialog?.kind === "delete" && saved ? (
        <ConfirmDelete
          name={saved.name}
          isDefault={saved.isDefault}
          onClose={close}
          onConfirm={() =>
            run(async () => {
              await deleteDashboard(saved.id);
              return { go: "start" };
            })
          }
        />
      ) : null}
      {dialog?.kind === "failed" && dialog.title === "restore" && saved && restoreTemplate ? (
        <Modal title={`Restore ${restoreTemplate.name} defaults?`} onClose={close}>
          <div className="dg-modal-form">
            <p className="dg-modal-text">
              Replace the current layout with {restoreTemplate.name} version {restoreTemplate.version}.
              Nothing is saved until you choose Done.
            </p>
            <div className="dg-modal-actions">
              <button type="button" className="action secondary" onClick={close}>
                Cancel
              </button>
              <button
                type="button"
                className="action"
                onClick={() => {
                  onResetLayout?.(restoreTemplate.layout);
                  close();
                }}
              >
                Restore defaults
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
      {dialog?.kind === "failed" && dialog.title === "reset" && saved ? (
        <Modal title={`Reset "${saved.name}" to empty?`} onClose={close}>
          <div className="dg-modal-form">
            <p className="dg-modal-text">
              Remove every widget from the current layout. Nothing is saved until you choose Done.
            </p>
            <div className="dg-modal-actions">
              <button type="button" className="action secondary" onClick={close}>
                Cancel
              </button>
              <button
                type="button"
                className="action"
                onClick={() => {
                  onResetLayout?.({ v: 1, range: "24h", widgets: [] });
                  close();
                }}
              >
                Reset to empty
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
      {dialog?.kind === "failed" && dialog.title !== "restore" && dialog.title !== "reset" ? (
        <Modal title={dialog.title} onClose={close}>
          <div className="dg-modal-form">
            <p className="dg-modal-error" role="alert">
              {dialog.text}
            </p>
            <div className="dg-modal-actions">
              <button type="button" className="action secondary" onClick={close}>
                Close
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
