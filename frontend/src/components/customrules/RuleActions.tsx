"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, type CustomRule } from "@/lib/api";

export function RuleActions({ rule }: { rule: CustomRule }) {
  const router = useRouter();
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
      setError(`The rule could not be ${rule.enabled ? "disabled" : "enabled"}. Reload it and try again.`);
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
      setError("The rule could not be deleted. Reload it and try again.");
      setPending(false);
    }
  }

  return (
    <div className="cr-actions">
      <button type="button" className="action secondary" disabled={pending} onClick={() => void toggle()}>
        {pending ? "Saving…" : rule.enabled ? "Disable" : "Enable"}
      </button>
      {!confirming ? (
        <button type="button" className="action secondary" disabled={pending} onClick={() => setConfirming(true)}>
          Delete
        </button>
      ) : (
        <div className="cr-delete-confirm" role="alert">
          <p>Delete this rule? If it has an open alert, the alert will be resolved before the rule and its breach history are removed.</p>
          <div className="actions">
            <button type="button" className="action" disabled={pending} onClick={() => void remove()}>Delete rule</button>
            <button type="button" className="action secondary" disabled={pending} onClick={() => setConfirming(false)}>Keep rule</button>
          </div>
        </div>
      )}
      {error && <p className="field-error" role="alert">{error}</p>}
    </div>
  );
}
