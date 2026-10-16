"use client";

// Enable or disable one stored search. The patch is `{enabled}` only, so the
// audit row is search.enable or search.disable. A 409 names the enabled-search
// cap; the control adds what to do and does not rewrite the limit.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { capMessage, classifySaveError } from "@/components/searches/authoring";

import "./searches.css";

export function SearchEnableControl({ id, enabled }: { id: string; enabled: boolean }) {
  const router = useRouter();
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
      const refusal = classifySaveError(err);
      if (refusal.kind === "cap") setCap(capMessage(refusal.detail));
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
        {pending ? "Saving…" : enabled ? "Disable search" : "Enable search"}
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
