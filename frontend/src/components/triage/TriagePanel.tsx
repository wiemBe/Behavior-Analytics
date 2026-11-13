"use client";

// The analyst's triage of one anomaly: who has it, what was decided, and what happened.
//
// It is the interactive island of the anomaly page, so the page stays a server component and hands
// across primitives: the family, the id, the first read of the triage (or the fact that it failed),
// the caller's access and name. The triage status is the analyst's; the detector's lifecycle state
// is the page header's, and nothing here changes it.
//
// Every change sends `expected_version`. A 409 means someone else wrote first: the panel fetches
// their version, says who changed what and when, switches its own controls off, and applies the
// other version only when the analyst asks. Nothing is overwritten silently.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { UtcTime } from "@/components/ui/UtcTime";
import { ApiError, actionErrorMessage, api } from "@/lib/api";
import type {
  AnalystTriageDisposition,
  Triage,
  TriageFamily,
  TriagePatch,
} from "@/lib/api";
import {
  ACTIVITY_PAGE,
  CLOSE_DISPOSITIONS,
  COMMENT_MAX,
  DISPOSITIONS,
  NOTE_MAX,
  OWNER_MAX,
  eventSentence,
} from "@/lib/triage";
import { asTextTranslator } from "@/i18n/textTranslator";

import "./triage.css";

export type TriageAccess = "write" | "read" | "unknown";

/** The alert that names this anomaly in `source_anomaly_ids`, when the page found one; its words are
 *  already the interface's (title, severity and status as the alert list shows them). */
export interface TriageAlertRef {
  id: string;
  title: string;
  severity: string;
  status: string;
}

export interface TriagePanelProps {
  family: TriageFamily;
  anomalyId: string;
  /** The first read, or `null` when it failed (then `loadFailed` explains it). */
  initial: Triage | null;
  access: TriageAccess;
  /** The caller's subject: what "Take it" writes as the owner. `null` when unknown. */
  me: string | null;
  /** `undefined`: not looked up. `null`: looked up completely, none references this anomaly. */
  alert?: TriageAlertRef | null;
  /** More of the analyst's decisions about this anomaly, below the activity (SOC-13d). */
  extra?: ReactNode;
}

type Mode = "assign" | "close" | null;

export function TriagePanel({ family, anomalyId, initial, access, me, alert, extra }: TriagePanelProps) {
  const t = useTranslations("triage");
  const say = asTextTranslator(useTranslations());
  const ids = useId();
  const [triage, setTriage] = useState<Triage | null>(initial);
  const [loadFailed, setLoadFailed] = useState(initial === null);
  const [busy, setBusy] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>(null);
  const [ownerDraft, setOwnerDraft] = useState("");
  const [dispositionDraft, setDispositionDraft] = useState<AnalystTriageDisposition | "">("");
  const [noteDraft, setNoteDraft] = useState("");
  const [commentDraft, setCommentDraft] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ current: Triage | null } | null>(null);
  const [activityError, setActivityError] = useState(false);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const assignRef = useRef<HTMLButtonElement | null>(null);
  const dispositionRef = useRef<HTMLSelectElement | null>(null);
  const ownerRef = useRef<HTMLInputElement | null>(null);
  const restore = useRef<Mode>(null);

  // Focus goes into a form when it opens and back to its button when it closes.
  useEffect(() => {
    if (mode === "close") dispositionRef.current?.focus();
    else if (mode === "assign") ownerRef.current?.focus();
    else if (restore.current === "close") closeRef.current?.focus();
    else if (restore.current === "assign") assignRef.current?.focus();
    if (mode === null) restore.current = null;
  }, [mode]);

  // `n` on the anomaly list lands here with `#triage-note` (SOC-15b): focus the comment field, and
  // only when the panel really has one (a read-only viewer has none).
  const commentRef = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    if (window.location.hash !== "#triage-note") return;
    const field = commentRef.current;
    field?.focus();
    field?.scrollIntoView?.({ block: "center" });
  }, []);

  const word = {
    status: (value: string) => t(`status.${value}` as never),
    disposition: (value: string) =>
      (DISPOSITIONS as readonly string[]).includes(value) ? t(`disposition.${value}` as never) : value,
    unassigned: t("panel.unassigned"),
  };
  const sentence = (key: string, values?: Record<string, string>) =>
    t(key as never, values as never);

  function closeForms() {
    restore.current = mode;
    setMode(null);
    setFormError(null);
  }

  async function reloadPage(offset: number) {
    setActivityError(false);
    setBusy("activity");
    try {
      const next = await api.triage(family, anomalyId, { limit: ACTIVITY_PAGE, offset });
      // Only the activity page: the fields and the version stay what the analyst is working from.
      setTriage((cur) => (cur ? { ...cur, events: next.events } : next));
    } catch {
      setActivityError(true);
    } finally {
      setBusy(null);
    }
  }

  async function retryLoad() {
    setBusy("load");
    try {
      setTriage(await api.triage(family, anomalyId, { limit: ACTIVITY_PAGE }));
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    } finally {
      setBusy(null);
    }
  }

  async function change(label: string, body: Omit<TriagePatch, "expected_version">) {
    if (busy || !triage) return;
    setBusy(label);
    setFormError(null);
    setNotice(null);
    try {
      const next = await api.patchTriage(family, anomalyId, {
        ...body,
        expected_version: triage.version,
      });
      setTriage(next);
      setMode(null);
      restore.current = null;
      setNotice(t("panel.saved"));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        let current: Triage | null = null;
        try {
          current = await api.triage(family, anomalyId, { limit: ACTIVITY_PAGE });
        } catch {
          current = null;
        }
        setConflict({ current });
      } else {
        setFormError(actionErrorMessage(err, say));
      }
    } finally {
      setBusy(null);
    }
  }

  async function addComment() {
    if (busy || !triage) return;
    const text = commentDraft.trim();
    if (!text) {
      setFormError(t("panel.commentEmpty"));
      return;
    }
    setBusy("comment");
    setFormError(null);
    setNotice(null);
    try {
      const event = await api.commentTriage(family, anomalyId, text);
      setCommentDraft("");
      setNotice(t("panel.commentSaved"));
      setTriage((cur) =>
        cur && cur.events.offset === 0
          ? {
              ...cur,
              events: {
                ...cur.events,
                items: [event, ...cur.events.items].slice(0, cur.events.limit),
                total: cur.events.total + 1,
              },
            }
          : cur,
      );
      if (triage.events.offset !== 0) await reloadPage(0);
    } catch (err) {
      setFormError(actionErrorMessage(err, say));
    } finally {
      setBusy(null);
    }
  }

  function submitClose() {
    if (!dispositionDraft) {
      setFormError(t("panel.closeNeedsDisposition"));
      dispositionRef.current?.focus();
      return;
    }
    const note = noteDraft.trim();
    void change("close", {
      status: "CLOSED",
      disposition: dispositionDraft,
      ...(note ? { disposition_note: note } : {}),
    });
  }

  function applyTheirs() {
    if (conflict?.current) {
      setTriage(conflict.current);
      setLoadFailed(false);
    }
    setConflict(null);
    setMode(null);
    setFormError(null);
    setNotice(null);
  }

  const heading = (
    <h2 className="section-title" id={`${ids}-h`}>
      {t("panel.heading")}
    </h2>
  );

  if (!triage) {
    return (
      <section className="card triage" aria-labelledby={`${ids}-h`}>
        {heading}
        <p role="status" className="triage-problem">
          {t("panel.loadFailed")}
        </p>
        {loadFailed && (
          <button
            type="button"
            className="action secondary"
            disabled={busy === "load"}
            onClick={() => void retryLoad()}
          >
            {t("panel.retry")}
          </button>
        )}
        {extra}
      </section>
    );
  }

  const writable = access === "write" && !conflict;
  const closed = triage.status === "CLOSED";
  const events = triage.events;
  const from = events.total === 0 ? 0 : events.offset + 1;
  const to = Math.min(events.offset + events.items.length, events.total);
  const theirs = conflict?.current ?? null;
  const ownedByMe = me !== null && triage.owner === me;

  return (
    <section className="card triage" aria-labelledby={`${ids}-h`}>
      {heading}
      <p className="subtitle triage-lead">{t("panel.lead")}</p>

      {conflict && (
        <div className="triage-conflict" role="alert">
          <p>
            <strong>{t("panel.conflictTitle")}</strong>
          </p>
          {theirs ? (
            <>
              <p>
                {t.rich("panel.conflictBody", {
                  actor: theirs.updated_by ?? "—",
                  status: word.status(theirs.status),
                  owner: theirs.owner
                    ? t("panel.conflictOwner", { owner: theirs.owner })
                    : t("panel.conflictNoOwner"),
                  time: () => <UtcTime value={theirs.updated_at} />,
                })}
              </p>
              <button type="button" className="action" onClick={applyTheirs}>
                {t("panel.conflictReload")}
              </button>
            </>
          ) : (
            <p>{t("panel.conflictUnread")}</p>
          )}
        </div>
      )}

      <div className="triage-grid">
        <div className="triage-state">
          <dl className="dimension-meta triage-facts">
            <div>
              <dt>{t("panel.statusLabel")}</dt>
              <dd>
                <span
                  className={`pill pill-strong ${
                    triage.status === "CLOSED" ? "" : triage.status === "IN_PROGRESS" ? "info" : "warn"
                  }`}
                >
                  {word.status(triage.status)}
                </span>
              </dd>
            </div>
            <div>
              <dt>{t("panel.ownerLabel")}</dt>
              <dd className={triage.owner ? "triage-owner" : "muted"}>
                {triage.owner ?? t("panel.unassigned")}
              </dd>
            </div>
            {triage.disposition && (
              <div>
                <dt>{t("panel.dispositionLabel")}</dt>
                <dd>{word.disposition(triage.disposition)}</dd>
              </div>
            )}
          </dl>
          {triage.disposition_note && (
            <p className="triage-note">
              <span className="triage-note-label">{t("panel.noteLabel")}</span>
              {triage.disposition_note}
            </p>
          )}
          <p className="subtitle triage-updated">
            {triage.updated_at
              ? t.rich("panel.updated", {
                  actor: triage.updated_by ?? "—",
                  time: () => <UtcTime value={triage.updated_at} />,
                })
              : t("panel.untouched")}
          </p>
          {alert !== undefined && (
            <p className="subtitle triage-alert">
              {alert
                ? t.rich("panel.alertFound", {
                    title: alert.title,
                    severity: alert.severity,
                    status: alert.status,
                    link: (chunks) => <Link href={`/alerts/${alert.id}`}>{chunks}</Link>,
                  })
                : t("panel.alertNone")}
            </p>
          )}

          {access === "read" && <p className="triage-readonly">{t("panel.readOnly")}</p>}
          {access === "unknown" && <p className="triage-readonly">{t("panel.accessUnknown")}</p>}

          {access === "write" && (
            <div className="actions triage-actions">
              {closed ? (
                <button
                  type="button"
                  className="action"
                  disabled={!writable || busy !== null}
                  onClick={() =>
                    void change("reopen", {
                      status: "IN_PROGRESS",
                      disposition: null,
                      disposition_note: null,
                    })
                  }
                >
                  {busy === "reopen" ? t("panel.saving") : t("panel.reopen")}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="action"
                    disabled={!writable || busy !== null || me === null || (ownedByMe && triage.status === "IN_PROGRESS")}
                    title={t("panel.takeItHelp")}
                    onClick={() =>
                      me !== null && void change("take", { owner: me, status: "IN_PROGRESS" })
                    }
                  >
                    {busy === "take" ? t("panel.saving") : t("panel.takeIt")}
                  </button>
                  <button
                    type="button"
                    ref={assignRef}
                    className="action secondary"
                    disabled={!writable || busy !== null}
                    aria-expanded={mode === "assign"}
                    onClick={() => {
                      setOwnerDraft(triage.owner ?? "");
                      setFormError(null);
                      setMode(mode === "assign" ? null : "assign");
                    }}
                  >
                    {t("panel.assign")}
                  </button>
                  <button
                    type="button"
                    ref={closeRef}
                    className="action secondary"
                    disabled={!writable || busy !== null}
                    aria-expanded={mode === "close"}
                    onClick={() => {
                      setFormError(null);
                      setMode(mode === "close" ? null : "close");
                    }}
                  >
                    {t("panel.closeOpen")}
                  </button>
                </>
              )}
            </div>
          )}

          {mode === "assign" && writable && !closed && (
            <form
              className="triage-form"
              aria-label={t("panel.assign")}
              onSubmit={(e) => {
                e.preventDefault();
                void change("assign", { owner: ownerDraft.trim() || null });
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") closeForms();
              }}
            >
              <label htmlFor={`${ids}-owner`}>{t("panel.assignLabel", { max: OWNER_MAX })}</label>
              <input
                id={`${ids}-owner`}
                ref={ownerRef}
                className="field"
                type="text"
                maxLength={OWNER_MAX}
                value={ownerDraft}
                onChange={(e) => setOwnerDraft(e.target.value)}
                aria-describedby={`${ids}-owner-hint`}
              />
              <p id={`${ids}-owner-hint`} className="field-hint">
                {t("panel.assignHint")}
              </p>
              <div className="actions">
                <button type="submit" className="action" disabled={busy !== null}>
                  {busy === "assign" ? t("panel.saving") : t("panel.assignSave")}
                </button>
                <button type="button" className="action secondary" onClick={closeForms}>
                  {t("panel.cancel")}
                </button>
              </div>
            </form>
          )}

          {mode === "close" && writable && !closed && (
            <form
              className="triage-form"
              aria-label={t("panel.closeHeading")}
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                submitClose();
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") closeForms();
              }}
            >
              <label htmlFor={`${ids}-disp`}>{t("panel.closeDisposition")}</label>
              <select
                id={`${ids}-disp`}
                ref={dispositionRef}
                className="field"
                value={dispositionDraft}
                aria-required="true"
                aria-invalid={formError === t("panel.closeNeedsDisposition")}
                onChange={(e) => {
                  setDispositionDraft(e.target.value as AnalystTriageDisposition | "");
                  setFormError(null);
                }}
              >
                <option value="">{t("panel.closeChoose")}</option>
                {CLOSE_DISPOSITIONS.map((d) => (
                  <option key={d} value={d}>
                    {word.disposition(d)}
                  </option>
                ))}
              </select>
              <label htmlFor={`${ids}-note`}>{t("panel.closeNote", { max: NOTE_MAX })}</label>
              <textarea
                id={`${ids}-note`}
                className="field"
                rows={3}
                maxLength={NOTE_MAX}
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
              />
              <p className="field-hint triage-count">
                {t("panel.count", { used: noteDraft.length, max: NOTE_MAX })}
              </p>
              <div className="actions">
                <button type="submit" className="action" disabled={busy !== null}>
                  {busy === "close" ? t("panel.saving") : t("panel.closeConfirm")}
                </button>
                <button type="button" className="action secondary" onClick={closeForms}>
                  {t("panel.cancel")}
                </button>
              </div>
            </form>
          )}

          {formError && (
            <p role="alert" className="pill crit triage-result">
              {formError}
            </p>
          )}
          {notice && !formError && (
            <p role="status" className="pill ok triage-result">
              {notice}
            </p>
          )}
        </div>

        <div className="triage-activity">
          <h3 className="triage-subheading" id={`${ids}-act`}>
            {t("panel.activityHeading")}
          </h3>
          {activityError ? (
            <p role="alert" className="triage-problem">
              {t("panel.activityFailed")}
            </p>
          ) : events.items.length === 0 ? (
            <p className="subtitle">{t("panel.activityEmpty")}</p>
          ) : (
            <ol
              className="triage-events"
              aria-labelledby={`${ids}-act`}
              aria-busy={busy === "activity"}
            >
              {events.items.map((event) => (
                <li key={event.id} className={`triage-event kind-${event.kind.toLowerCase()}`}>
                  <div className="triage-event-head">
                    <UtcTime value={event.created_at} variant="compact" />
                    <span className="triage-actor">{event.actor}</span>
                  </div>
                  {event.kind !== "COMMENT" && (
                    <p className="triage-event-line">{eventSentence(event, word, sentence)}</p>
                  )}
                  {event.comment && <p className="triage-event-text">{event.comment}</p>}
                </li>
              ))}
            </ol>
          )}
          {events.total > events.limit && (
            <nav className="triage-pager" aria-label={t("panel.activityLabel")}>
              <button
                type="button"
                className="action secondary"
                disabled={busy !== null || events.offset === 0}
                onClick={() => void reloadPage(Math.max(0, events.offset - events.limit))}
              >
                {t("panel.newer")}
              </button>
              <span className="muted num">
                {t("panel.activityRange", { from, to, total: events.total })}
              </span>
              <button
                type="button"
                className="action secondary"
                disabled={busy !== null || events.offset + events.limit >= events.total}
                onClick={() => void reloadPage(events.offset + events.limit)}
              >
                {t("panel.older")}
              </button>
            </nav>
          )}

          {access === "write" && (
            <form
              id="triage-note"
              className="triage-form triage-comment"
              aria-label={t("panel.commentAdd")}
              onSubmit={(e) => {
                e.preventDefault();
                void addComment();
              }}
            >
              <label htmlFor={`${ids}-comment`}>{t("panel.commentLabel", { max: COMMENT_MAX })}</label>
              <textarea
                id={`${ids}-comment`}
                ref={commentRef}
                className="field"
                rows={3}
                maxLength={COMMENT_MAX}
                value={commentDraft}
                disabled={!writable}
                onChange={(e) => setCommentDraft(e.target.value)}
              />
              <div className="actions">
                <button type="submit" className="action secondary" disabled={!writable || busy !== null}>
                  {busy === "comment" ? t("panel.saving") : t("panel.commentAdd")}
                </button>
                <span className="field-hint triage-count">
                  {t("panel.count", { used: commentDraft.length, max: COMMENT_MAX })}
                </span>
              </div>
            </form>
          )}
        </div>
      </div>
      {extra}
    </section>
  );
}

/** Shown in place of the panel for an anomaly whose kind has no triage. */
export function TriageUnavailable() {
  const t = useTranslations("triage");
  return (
    <section className="card triage" aria-label={t("panel.heading")}>
      <p className="subtitle">{t("panel.unavailable")}</p>
    </section>
  );
}
