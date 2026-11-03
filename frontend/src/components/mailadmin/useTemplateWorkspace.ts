"use client";

// The template editor's state, held above the page's tabs so that switching to Recipients and back
// loses nothing: each template keeps its own draft, its preview and its save result. Nothing is
// written until Save; a draft that differs from what is stored is "unsaved" and is flagged.

import { useCallback, useEffect, useRef, useState } from "react";

import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { errorTexts } from "@/components/auth/reasons";
import {
  ApiError,
  mailAdminApi,
  type EmailPreview,
  type EmailTemplate,
} from "@/lib/api";

import { bodyOf, draftFrom, sameDraft, type Draft } from "./mail";

export interface PreviewReady {
  kind: "ok";
  data: EmailPreview;
  /** What the preview was built from, so the page can say when the editor has moved on. */
  draft: Draft;
  sample: string | null;
}

export type PreviewView =
  | { kind: "idle" }
  /** `previous` stays on screen while the next one is built. */
  | { kind: "loading"; previous: PreviewReady | null }
  | PreviewReady
  | { kind: "failed"; lines: string[] };

export type SaveView =
  | { kind: "saved"; sanitiserChanged: boolean }
  | { kind: "failed"; lines: string[] };

export type ResetPhase = "idle" | "confirm" | "working";

export interface Editor {
  load: "loading" | "ok" | "error" | "forbidden";
  httpStatus: number | null;
  baseline: EmailTemplate | null;
  draft: Draft | null;
  /** The alert type whose fixed sample fills the preview; null is the generic sample. */
  sample: string | null;
  saving: boolean;
  save: SaveView | null;
  preview: PreviewView;
  reset: ResetPhase;
  resetLines: string[];
}

export type TestView =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "outcome"; ok: boolean; code: string; message: string }
  | { kind: "limited"; retryAfter: number | null }
  | { kind: "failed"; lines: string[] };

const EMPTY: Editor = {
  load: "loading",
  httpStatus: null,
  baseline: null,
  draft: null,
  sample: null,
  saving: false,
  save: null,
  preview: { kind: "idle" },
  reset: "idle",
  resetLines: [],
};

export function isDirty(editor: Editor | undefined): boolean {
  return !!editor && editor.baseline !== null && editor.draft !== null
    ? !sameDraft(editor.draft, draftFrom(editor.baseline))
    : false;
}

export function previewIsStale(editor: Editor): boolean {
  const view = editor.preview;
  if (view.kind !== "ok" || editor.draft === null) return false;
  return !sameDraft(view.draft, editor.draft) || view.sample !== editor.sample;
}

export function useTemplateWorkspace(onListChanged: () => void) {
  const { t: say, locale } = useBehaviorI18n();
  const [selected, setSelected] = useState("default");
  const [editors, setEditors] = useState<Record<string, Editor>>({});
  const [test, setTest] = useState<TestView>({ kind: "idle" });
  const editorsRef = useRef(editors);
  editorsRef.current = editors;
  const previewSeq = useRef<Record<string, number>>({});
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const patch = useCallback((key: string, change: Partial<Editor>) => {
    if (!mounted.current) return;
    setEditors((all) => ({ ...all, [key]: { ...(all[key] ?? EMPTY), ...change } }));
  }, []);

  const runPreview = useCallback(
    async (key: string, override?: { draft?: Draft; sample?: string | null }) => {
      const editor = editorsRef.current[key];
      const draft = override?.draft ?? editor?.draft;
      if (!draft) return;
      const sample = override && "sample" in override ? (override.sample ?? null) : (editor?.sample ?? null);
      const seq = (previewSeq.current[key] = (previewSeq.current[key] ?? 0) + 1);
      const shown = editor?.preview;
      const previous = shown?.kind === "ok" ? shown : shown?.kind === "loading" ? shown.previous : null;
      patch(key, { preview: { kind: "loading", previous } });
      try {
        const data = await mailAdminApi.preview({ ...bodyOf(draft), alert_type: sample });
        if (previewSeq.current[key] === seq) patch(key, { preview: { kind: "ok", data, draft, sample } });
      } catch (err) {
        if (previewSeq.current[key] === seq) {
          patch(key, { preview: { kind: "failed", lines: errorTexts(err, say, locale) } });
        }
      }
    },
    [patch, say, locale],
  );

  const open = useCallback(
    async (key: string) => {
      patch(key, { load: "loading", httpStatus: null });
      try {
        const template = await mailAdminApi.template(key);
        const draft = draftFrom(template);
        // The sample the administrator chose stays across a reset; a first open starts from the
        // template's own alert type (the generic sample for the default).
        const sample = editorsRef.current[key]?.sample ?? template.alert_type;
        patch(key, {
          load: "ok",
          baseline: template,
          draft,
          sample,
          save: null,
          reset: "idle",
          resetLines: [],
        });
        await runPreview(key, { draft, sample });
      } catch (err) {
        patch(key, {
          load: err instanceof ApiError && err.status === 403 ? "forbidden" : "error",
          httpStatus: err instanceof ApiError ? err.status : null,
        });
      }
    },
    [patch, runPreview],
  );

  const select = useCallback(
    (key: string) => {
      setSelected(key);
      setTest({ kind: "idle" });
      const existing = editorsRef.current[key];
      if (!existing || existing.load === "error") void open(key);
    },
    [open],
  );

  // The first template, the default, opens with the page.
  useEffect(() => {
    void open("default");
  }, [open]);

  const edit = useCallback(
    (key: string, change: Partial<Draft>) => {
      const editor = editorsRef.current[key];
      if (!editor?.draft) return;
      patch(key, { draft: { ...editor.draft, ...change }, save: null });
    },
    [patch],
  );

  const discard = useCallback(
    (key: string) => {
      const editor = editorsRef.current[key];
      if (!editor?.baseline) return;
      const draft = draftFrom(editor.baseline);
      patch(key, { draft, save: null });
      void runPreview(key, { draft });
    },
    [patch, runPreview],
  );

  const setSample = useCallback(
    (key: string, sample: string | null) => {
      patch(key, { sample });
      void runPreview(key, { sample });
    },
    [patch, runPreview],
  );

  const save = useCallback(
    async (key: string) => {
      const editor = editorsRef.current[key];
      if (!editor?.draft || editor.saving) return;
      const typed = editor.draft.html;
      patch(key, { saving: true, save: null });
      try {
        const stored = await mailAdminApi.saveTemplate(key, bodyOf(editor.draft));
        const draft = draftFrom(stored);
        patch(key, {
          saving: false,
          baseline: stored,
          draft,
          save: { kind: "saved", sanitiserChanged: stored.html_body !== typed },
        });
        onListChanged();
        void runPreview(key, { draft });
      } catch (err) {
        patch(key, { saving: false, save: { kind: "failed", lines: errorTexts(err, say, locale) } });
      }
    },
    [patch, onListChanged, runPreview, say, locale],
  );

  const askReset = useCallback((key: string) => patch(key, { reset: "confirm", resetLines: [] }), [patch]);
  const cancelReset = useCallback((key: string) => patch(key, { reset: "idle", resetLines: [] }), [patch]);

  const reset = useCallback(
    async (key: string) => {
      patch(key, { reset: "working", resetLines: [] });
      try {
        await mailAdminApi.resetTemplate(key);
      } catch (err) {
        // A row that is already gone is the state the reset asked for.
        if (!(err instanceof ApiError && err.status === 404)) {
          patch(key, { reset: "confirm", resetLines: errorTexts(err, say, locale) });
          return;
        }
      }
      onListChanged();
      // What the key sends now is the next layer down; the editor starts again from it.
      patch(key, { draft: null, baseline: null, save: null });
      await open(key);
    },
    [patch, onListChanged, open, say, locale],
  );

  const sendTest = useCallback(
    async (key: string, to: string) => {
      const editor = editorsRef.current[key];
      if (!editor?.draft) return;
      setTest({ kind: "sending" });
      try {
        const result = await mailAdminApi.sendTest({
          ...bodyOf(editor.draft),
          alert_type: editor.sample,
          to,
        });
        if (mounted.current) {
          setTest({
            kind: "outcome",
            ok: result.ok,
            code: result.outcome.code,
            message: result.outcome.message,
          });
        }
      } catch (err) {
        if (!mounted.current) return;
        if (err instanceof ApiError && err.status === 429) {
          setTest({ kind: "limited", retryAfter: err.retryAfter });
        } else {
          setTest({ kind: "failed", lines: errorTexts(err, say, locale) });
        }
      }
    },
    [say, locale],
  );

  const dirtyKeys = Object.keys(editors).filter((key) => isDirty(editors[key]));

  return {
    selected,
    editors,
    test,
    dirtyKeys,
    select,
    edit,
    discard,
    setSample,
    save,
    askReset,
    cancelReset,
    reset,
    refreshPreview: runPreview,
    sendTest,
    reopen: open,
  };
}

export type TemplateWorkspace = ReturnType<typeof useTemplateWorkspace>;
