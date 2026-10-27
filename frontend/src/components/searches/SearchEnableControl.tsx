"use client";

// Enable or disable one stored search. The patch is `{enabled}` only, so the
// audit row is search.enable or search.disable. A 409 names the enabled-search
// cap; the control adds what to do and does not rewrite the limit.

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { api } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { capMessage, classifySaveError } from "@/components/searches/authoring";

import "./searches.css";

export function SearchEnableControl({ id, enabled }: { id: string; enabled: boolean }) {
  const router = useRouter();
  const t = useTranslations("searches.enable");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const [pending, setPending] = useState(false);
  const [cap, setCap] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (pending) return;
    setPending(true);
    setCap(null);
    setError(null);
    try {
      await api.setSearchEnabled(id, !enabled);
      router.refresh();
    } catch (err) {
      const refusal = classifySaveError(err, i18n);
      if (refusal.kind === "cap") setCap(capMessage(refusal.detail, i18n));
      else setError(refusal.kind === "reasons" ? refusal.reasons.join(" ") : refusal.detail);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="search-enable">
      <button
        type="button"
        className="action secondary"
        onClick={() => void toggle()}
        disabled={pending}
        aria-busy={pending}
      >
        {pending ? common("saving") : enabled ? t("disable") : t("enable")}
      </button>
      {cap && (
        <p className="banner crit" role="alert">
          {cap}
        </p>
      )}
      {error && (
        <p className="banner crit" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
