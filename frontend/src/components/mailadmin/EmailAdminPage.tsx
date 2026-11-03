"use client";

// "What will our alert e-mail look like, who gets it, and does it reach them?" The relay summary
// comes first, because nothing below it matters when no mail can leave. Templates and recipients
// are two tabs; the template editor's state and the recipient form's draft live above them, so a
// draft survives a switch.

import { useTranslations } from "next-intl";
import { useCallback, useState } from "react";

import { Tabs } from "@/components/ui/Tabs";
import { mailAdminApi } from "@/lib/api";

import { isRecipientDirty, type RecipientDraft } from "./recipientDraft";
import { RecipientsPanel } from "./RecipientsPanel";
import { RelaySection } from "./RelaySection";
import { TemplatesPanel } from "./TemplatesPanel";
import { useLeaveGuard } from "./useLeaveGuard";
import { usePart } from "./usePart";
import { useTemplateWorkspace } from "./useTemplateWorkspace";

import "./mailadmin.css";

export function EmailAdminPage() {
  const t = useTranslations("mailAdmin.page");
  const settings = usePart(mailAdminApi.settings);
  const templates = usePart(mailAdminApi.templates);
  const placeholders = usePart(mailAdminApi.placeholders);
  const recipients = usePart(mailAdminApi.recipients);
  const reloadTemplates = templates.reload;
  const onListChanged = useCallback(() => reloadTemplates(), [reloadTemplates]);
  const workspace = useTemplateWorkspace(onListChanged);
  const [recipientDraft, setRecipientDraft] = useState<RecipientDraft | null>(null);
  const templatesUnsaved = workspace.dirtyKeys.length > 0;
  const recipientUnsaved = isRecipientDirty(recipientDraft);
  useLeaveGuard(
    templatesUnsaved || recipientUnsaved,
    templatesUnsaved && recipientUnsaved
      ? t("leaveBoth")
      : recipientUnsaved
        ? t("leaveRecipient")
        : t("leaveConfirm"),
  );

  const parts = [settings.part, templates.part, placeholders.part, recipients.part];
  if (parts.some((part) => part.status === "forbidden")) {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("forbidden.heading")}</strong>
        </p>
        <p>{t.rich("forbidden.body", { perm: (chunks) => <code className="perm">{chunks}</code> })}</p>
      </div>
    );
  }

  const alertTypes = templates.part.status === "ok" ? templates.part.data.alert_types : null;

  return (
    <div className="mail-page">
      <RelaySection part={settings.part} onRetry={settings.reload} />
      <Tabs
        label={t("tabsLabel")}
        tabs={[
          {
            id: "templates",
            label: templatesUnsaved ? t("tabTemplatesUnsaved") : t("tabTemplates"),
            content: () => (
              <TemplatesPanel
                templates={templates.part}
                placeholders={placeholders.part}
                settings={settings.part}
                workspace={workspace}
                onRetry={templates.reload}
              />
            ),
          },
          {
            id: "recipients",
            label: recipientUnsaved ? t("tabRecipientsUnsaved") : t("tabRecipients"),
            content: () => (
              <RecipientsPanel
                part={recipients.part}
                alertTypes={alertTypes}
                draft={recipientDraft}
                onDraft={setRecipientDraft}
                onChanged={recipients.reload}
                onRetry={recipients.reload}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
