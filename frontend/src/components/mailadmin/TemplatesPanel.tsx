"use client";

// The Templates tab: the list, the editor of the chosen template, and beside it the preview and the
// test send. All state lives in the workspace above the tabs.

import { useTranslations } from "next-intl";
import { useCallback, useRef } from "react";

import type { EmailPlaceholders, EmailSettings, EmailTemplateList } from "@/lib/api";

import { PartError } from "./PartError";
import { PreviewPane } from "./PreviewPane";
import { PlaceholderList, TemplateEditor } from "./TemplateEditor";
import { TemplateTable } from "./TemplateTable";
import { TestSend } from "./TestSend";
import type { Part } from "./usePart";
import type { TemplateWorkspace } from "./useTemplateWorkspace";

export function TemplatesPanel({
  templates,
  placeholders,
  settings,
  workspace,
  onRetry,
}: {
  templates: Part<EmailTemplateList>;
  placeholders: Part<EmailPlaceholders>;
  settings: Part<EmailSettings>;
  workspace: TemplateWorkspace;
  onRetry: () => void;
}) {
  const t = useTranslations("mailAdmin.templates");
  const insertRef = useRef<((name: string) => void) | null>(null);
  const onInsertReady = useCallback((insert: ((name: string) => void) | null) => {
    insertRef.current = insert;
  }, []);
  if (templates.status === "loading") {
    return (
      <p className="notice" role="status" aria-live="polite">
        {t("loading")}
      </p>
    );
  }
  if (templates.status === "error") {
    return (
      <PartError
        what={t("what")}
        httpStatus={templates.httpStatus}
        retryAfter={templates.retryAfter}
        onRetry={onRetry}
      />
    );
  }
  if (templates.status !== "ok") return null;

  const list = templates.data;
  const editor = workspace.editors[workspace.selected];
  const ready = !!editor && editor.load === "ok" && editor.draft !== null;
  return (
    <div className="mail-templates">
      <section className="mail-section" aria-labelledby="mail-templates-h">
        <h2 className="mail-h" id="mail-templates-h">
          {t("heading")}
        </h2>
        <p className="mail-hint">{t("intro")}</p>
        <TemplateTable
          list={list}
          selected={workspace.selected}
          dirtyKeys={workspace.dirtyKeys}
          onSelect={workspace.select}
        />
      </section>
      <div className="mail-work">
        <TemplateEditor
          keyName={workspace.selected}
          editor={editor}
          list={list}
          workspace={workspace}
          onInsertReady={onInsertReady}
        />
        {ready ? (
          <div className="mail-placeholders-slot">
            <PlaceholderList
              placeholders={placeholders.status === "ok" ? placeholders.data : null}
              onInsert={(name) => insertRef.current?.(name)}
            />
          </div>
        ) : null}
        <div className="mail-side">
          {ready ? (
            <PreviewPane
              keyName={workspace.selected}
              editor={editor}
              alertTypes={list.alert_types}
              workspace={workspace}
            />
          ) : null}
          <TestSend
            configured={settings.status === "ok" ? settings.data.configured : null}
            limit={settings.status === "ok" ? settings.data.test_rate_limit : null}
            view={workspace.test}
            ready={ready}
            onSend={(to) => void workspace.sendTest(workspace.selected, to)}
          />
        </div>
      </div>
    </div>
  );
}
