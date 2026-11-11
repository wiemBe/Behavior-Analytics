"use client";

// Writing a handover: the note, the rows that would be frozen (preview), and a Watch checkbox
// on each (at most 20). Save posts the note and the checked keys; the page then shows the saved
// record. Nothing here changes triage or alerts, and a saved note cannot be edited.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { useBackendMessage } from "@/i18n/backendMessage";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  ApiError,
  actionErrorMessage,
  handoverApi,
  type HandoverBody,
  type HandoverRecord,
} from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import { MAX_WATCH, NOTE_MAX, noteProblem, sourceLines, watchKey } from "@/lib/handover";
import { refusalSentences } from "@/lib/refusal";

import { useWriteAccess } from "./access";
import { ItemsTable } from "./ItemsTable";
import { handoverLoadReason } from "./loadError";
import { RecordView } from "./RecordView";
import { SourceLines } from "./SourceLines";

import "@/components/ui/controls.css";
import "./handover.css";

type Preview =
  | { status: "loading" }
  | { status: "error"; reason: string }
  | { status: "ready"; body: HandoverBody };

export function ComposeView() {
  const t = useTranslations("handover.compose");
  const say = asTextTranslator(useTranslations());
  const backendMessage = useBackendMessage();
  const access = useWriteAccess();

  if (access === "unknown") {
    return (
      <p className="notice" role="status" aria-live="polite">
        {t("verifying")}
      </p>
    );
  }
  if (access === "no") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("forbiddenTitle")}</strong>
        </p>
        <p>{t("forbidden")}</p>
        <p>
          <Link href="/handover">{say("handover.back")}</Link>
        </p>
      </div>
    );
  }
  return <Form backendMessage={backendMessage} />;
}

function Form({ backendMessage }: { backendMessage: ReturnType<typeof useBackendMessage> }) {
  const t = useTranslations("handover.compose");
  const record = useTranslations("handover.record");
  const say = asTextTranslator(useTranslations());
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
  const [preview, setPreview] = useState<Preview>({ status: "loading" });
  const [nonce, setNonce] = useState(0);
  const [note, setNote] = useState("");
  const [watched, setWatched] = useState<ReadonlySet<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState<HandoverRecord | null>(null);
  const [tried, setTried] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setPreview({ status: "loading" });
    handoverApi
      .preview()
      .then((body) => {
        if (cancelled) return;
        setPreview({ status: "ready", body });
        // A row that left the snapshot cannot stay watched.
        setWatched((cur) => new Set([...cur].filter((k) => body.items.some((i) => watchKey(i) === k))));
      })
      .catch((err: unknown) => {
        if (!cancelled) setPreview({ status: "error", reason: handoverLoadReason(err, say) });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);

  const items = preview.status === "ready" ? preview.body.items : [];
  const problem = noteProblem(note);
  const count = (n: number) => formatCount(n, locale);
  const atLimit = watched.size >= MAX_WATCH;
  const lines = useMemo(() => (preview.status === "ready" ? sourceLines(preview.body) : []), [preview]);

  function toggle(key: string) {
    setWatched((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else if (next.size < MAX_WATCH) next.add(key);
      return next;
    });
  }

  function failureText(err: unknown): string {
    if (err instanceof ApiError) {
      if (err.status === 429) return actionErrorMessage(err, say);
      if (err.status === 401) return t("saveFailedExpired");
      const sentence = refusalSentences(err)[0];
      if (sentence?.code?.startsWith("handover.")) {
        return `${t("saveFailed")} ${backendMessage(sentence)}`;
      }
      if (err.status === 403) return t("saveFailedForbidden");
      if (err.status >= 500) return t("saveFailedServer");
      return `${t("saveFailed")} ${actionErrorMessage(err, say)}`;
    }
    return t("saveFailedNetwork");
  }

  async function save() {
    setTried(true);
    if (saving || problem !== null) return;
    setSaving(true);
    setFailure(null);
    try {
      const watch = items.filter((i) => watched.has(watchKey(i))).map((i) => ({ kind: i.kind, id: i.id }));
      setSaved(await handoverApi.create({ note, watch }));
    } catch (err) {
      setFailure(failureText(err));
    } finally {
      setSaving(false);
    }
  }

  if (saved) {
    const dropped = saved.body.watch_dropped;
    return (
      <div className="ho">
        <p className={`banner ${dropped.length > 0 ? "warn" : "ok"}`} role="status">
          {dropped.length > 0
            ? t("savedDropped", {
                count: count(dropped.length),
                keys: dropped.map((k) => `${record(`droppedKind.${k.kind}`)} ${k.id}`).join(", "),
              })
            : t("saved")}
        </p>
        <RecordView record={saved} latest />
        <p>
          <Link href="/handover">{say("handover.back")}</Link>
        </p>
      </div>
    );
  }

  const over = note.length - NOTE_MAX;
  return (
    <form
      className="ho ho-compose"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      noValidate
    >
      <div className="ho-field">
        <label htmlFor="ho-note" className="ho-h">
          {t("noteLabel")}
        </label>
        <p className="muted ho-hint" id="ho-note-hint">
          {t("noteHint")}
        </p>
        <textarea
          id="ho-note"
          className="ho-textarea"
          rows={6}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-describedby="ho-note-hint ho-note-count"
          aria-invalid={problem === "tooLong" || (tried && problem === "required") || undefined}
        />
        <p
          id="ho-note-count"
          className={`ho-counter num${problem === "tooLong" ? " ho-over" : ""}`}
          aria-live="polite"
        >
          {problem === "tooLong"
            ? t("noteCountOver", { count: count(note.length), max: count(NOTE_MAX), over: count(over) })
            : t("noteCount", { count: count(note.length), max: count(NOTE_MAX) })}
          {tried && problem === "required" && <span className="ho-over"> {t("noteRequired")}</span>}
        </p>
      </div>

      <section className="ho-section" aria-label={t("previewHeading")} aria-busy={preview.status === "loading"}>
        <h2 className="ho-h">
          {t("previewHeading")}{" "}
          {preview.status === "ready" && (
            <span className="ho-count num">{t("watchCount", { count: count(watched.size), max: count(MAX_WATCH) })}</span>
          )}
        </h2>
        {preview.status === "loading" ? (
          <p className="notice" role="status" aria-live="polite">
            {t("previewLoading")}
          </p>
        ) : preview.status === "error" ? (
          <div className="notice" role="alert">
            <p>{t("previewFailed")}</p>
            <p>{preview.reason}</p>
            <p>
              <button type="button" className="action secondary" onClick={() => setNonce((n) => n + 1)}>
                {say("handover.retry")}
              </button>
            </p>
          </div>
        ) : (
          <>
            {lines.length > 0 && <p className="banner warn">{t("partial")}</p>}
            {items.length === 0 ? (
              <p className="notice" role="status">
                {t("previewEmpty")}
              </p>
            ) : (
              <>
                {atLimit && (
                  <p className="banner warn" role="status">
                    {t("watchLimit", { max: count(MAX_WATCH) })}
                  </p>
                )}
                <ItemsTable
                  items={items}
                  label={t("previewHeading")}
                  preview
                  select={{
                    checked: (item) => watched.has(watchKey(item)),
                    disabled: (item) => !watched.has(watchKey(item)) && atLimit,
                    onToggle: (item) => toggle(watchKey(item)),
                  }}
                />
              </>
            )}
            <SourceLines lines={lines} preview />
          </>
        )}
      </section>

      {failure && (
        <p className="banner crit" role="alert">
          {failure}
        </p>
      )}
      <p className="ho-actions">
        <button type="submit" className="action" disabled={saving || problem === "tooLong"}>
          {saving ? t("saving") : t("save")}
        </button>{" "}
        <Link href="/handover">{t("cancel")}</Link>
      </p>
    </form>
  );
}
