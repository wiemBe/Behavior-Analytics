"use client";

// "Remove" for a WAF or IPS source: a confirm dialog, the delete, and the optional class clear.
//
// Removing deletes the profile only; collected history stays until retention. It works whatever
// state the log source or the profile is in. A class the operator set by hand (override) can be
// cleared in the same step; a class that comes from a rule is named and linked, never edited here.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";

import { ApiError, api, fleetApi, ipsApi, type ProfileClassOut } from "@/lib/api";

import "@/components/ui/controls.css";
import "./remove.css";

export type RemoveFamily = "WAF" | "IPS";

type ClassState =
  | { phase: "loading" }
  | { phase: "failed" }
  | { phase: "ready"; value: ProfileClassOut };

export function RemoveSource({
  family,
  logSourceId,
  name,
  label,
  onRemoved,
  onClassNotCleared,
}: {
  family: RemoveFamily;
  logSourceId: string;
  name: string;
  /** The trigger's text, already in the page's words ("Remove", "Remove from WAF"). */
  label: string;
  /** Called once the profile is gone, with the line to show the analyst. */
  onRemoved: (note: string | null) => void;
  onClassNotCleared?: () => void;
}) {
  const t = useTranslations("fieldMapping.remove");
  const common = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const clearId = useId();
  const [open, setOpen] = useState(false);
  const [classState, setClassState] = useState<ClassState>({ phase: "loading" });
  const [clearClass, setClearClass] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isFamilyClass = (value: string | null) => value === family;
  const loadClass = () =>
    family === "WAF" ? api.wafSourceClass(logSourceId) : ipsApi.sourceClass(logSourceId);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setClassState({ phase: "loading" });
    loadClass()
      .then((value) => alive && setClassState({ phase: "ready", value }))
      .catch(() => alive && setClassState({ phase: "failed" }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, family, logSourceId]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      cancelRef.current?.focus();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function close() {
    if (busy) return;
    setOpen(false);
    setError(null);
    triggerRef.current?.focus();
  }

  const value = classState.phase === "ready" ? classState.value : null;
  const inFamily = value !== null && isFamilyClass(value.source_class);
  const byOverride = inFamily && value.override_class === family;
  const byRule = inFamily && !byOverride;

  function reason(err: unknown): string {
    if (err instanceof ApiError && err.status === 403) {
      return t("forbidden", { permission: family === "WAF" ? "admin:waf" : "admin:ips" });
    }
    return t("failed", { family });
  }

  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      try {
        await (family === "WAF" ? api.removeWafProfile(logSourceId) : ipsApi.removeProfile(logSourceId));
      } catch (err) {
        // Already gone (another tab, another analyst): the goal is met, so the list refreshes.
        if (!(err instanceof ApiError && err.status === 404)) throw err;
      }
    } catch (err) {
      setError(reason(err));
      setBusy(false);
      return;
    }
    let note: string | null = null;
    if (byOverride && clearClass) {
      try {
        await fleetApi.setOverride(logSourceId, null);
      } catch (err) {
        note =
          err instanceof ApiError && err.status === 403
            ? t("classForbidden", { family, name })
            : t("classFailed", { family, name });
        onClassNotCleared?.();
      }
    }
    setBusy(false);
    setOpen(false);
    onRemoved(note);
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="action secondary"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
      <dialog
        ref={dialogRef}
        className="rm-dialog"
        aria-labelledby={titleId}
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
      >
        {open && (
          <>
            <h2 id={titleId} className="rm-title">
              {t("title", { name, family })}
            </h2>
            <p className="rm-text">{t("stays")}</p>
            {classState.phase === "failed" && <p className="fm-note">{t("classUnread")}</p>}
            {byOverride && (
              <div className="rm-check">
                <input
                  id={clearId}
                  type="checkbox"
                  checked={clearClass}
                  disabled={busy}
                  onChange={(event) => setClearClass(event.target.checked)}
                />
                <label htmlFor={clearId}>{t("clearClass", { family })}</label>
              </div>
            )}
            {byRule && (
              <p className="fm-note">
                {t("fromRule", { family })}{" "}
                <Link href="/admin/classification-rules">{t("rulesLink")}</Link>
              </p>
            )}
            {error && (
              <p className="fm-note is-crit" role="alert">
                {error}
              </p>
            )}
            <div className="rm-actions">
              <button
                ref={cancelRef}
                type="button"
                className="action secondary"
                disabled={busy}
                onClick={close}
              >
                {t("cancel")}
              </button>
              <button type="button" className="action" disabled={busy} onClick={() => void confirm()}>
                {busy ? common("saving") : t("confirm")}
              </button>
            </div>
          </>
        )}
      </dialog>
    </>
  );
}
