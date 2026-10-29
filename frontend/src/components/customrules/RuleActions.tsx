"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { api, type CustomRule } from "@/lib/api";

export function RuleActions({ rule }: { rule: CustomRule }) {
  const router = useRouter();
  const t = useTranslations("customRules.actions");
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.updateCustomRule(rule.id, {
        expected_updated_at: rule.updated_at,
        enabled: !rule.enabled,
      });
      router.refresh();
    } catch {
      setError(t("toggleFailed", {
        action: rule.enabled ? t("disabledAction") : t("enabledAction"),
      }));
    } finally {
      setPending(false);
    }
  }

  async function remove() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.deleteCustomRule(rule.id);
      router.push("/custom-rules");
    } catch {
      setError(t("deleteFailed"));
      setPending(false);
    }
  }

  return (
    <div className="cr-actions">
      <button type="button" className="action secondary" disabled={pending} onClick={() => void toggle()}>
        {pending ? t("saving") : rule.enabled ? t("disable") : t("enable")}
      </button>
      {!confirming ? (
        <button type="button" className="action secondary" disabled={pending} onClick={() => setConfirming(true)}>
          {t("delete")}
        </button>
      ) : (
        <div className="cr-delete-confirm" role="alert">
          <p>{t("confirm")}</p>
          <div className="actions">
            <button type="button" className="action" disabled={pending} onClick={() => void remove()}>{t("deleteRule")}</button>
            <button type="button" className="action secondary" disabled={pending} onClick={() => setConfirming(false)}>{t("keepRule")}</button>
          </div>
        </div>
      )}
      {error && <p className="field-error" role="alert">{error}</p>}
    </div>
  );
}
