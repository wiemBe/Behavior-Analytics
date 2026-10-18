"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, type PropertyWatch, type PropertyWatchCatalog } from "@/lib/api";

import { classifyWatchError } from "./authoring";

import "./propertywatch.css";

export function WatchActions({
  watch,
  catalog,
}: {
  watch: PropertyWatch;
  catalog: PropertyWatchCatalog | null;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState<"enable" | "delete" | null>(null);
  const [error, setError] = useState<React.ReactNode | null>(null);

  function renderRefusal(caught: unknown, fallback: string): React.ReactNode {
    const refusal = classifyWatchError(caught);
    if (refusal.kind === "dependencies") {
      return (
        <>
          {refusal.message}. Rules to detach first:{" "}
          {refusal.rules.map((rule, index) => (
            <span key={rule.id}>
              {index > 0 ? ", " : ""}
              <Link href={`/custom-rules/${rule.id}`}>{rule.name}</Link>
            </span>
          ))}
          .
        </>
      );
    }
    if (refusal.kind === "version") {
      return `This watch was changed by ${refusal.updatedBy ?? "another operator"} at ${refusal.currentVersion}. Reload it before trying again.`;
    }
    if (refusal.kind === "conflict") return refusal.detail;
    return fallback;
  }

  async function setEnabled(enabled: boolean) {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.updatePropertyWatch(watch.id, {
        expected_updated_at: watch.updated_at,
        enabled,
      });
      setConfirming(null);
      router.refresh();
    } catch (caught) {
      setConfirming(null);
      setError(
        renderRefusal(
          caught,
          `The watch could not be ${enabled ? "enabled" : "disabled"}. Reload it and try again.`,
        ),
      );
    } finally {
      setPending(false);
    }
  }

  async function remove() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.deletePropertyWatch(watch.id);
      router.push("/behavior/watches");
    } catch (caught) {
      setConfirming(null);
      setError(renderRefusal(caught, "The watch could not be deleted. Reload it and try again."));
      setPending(false);
    }
  }

  return (
    <div className="pw-actions">
      {watch.enabled ? (
        <button
          type="button"
          className="action secondary"
          disabled={pending}
          onClick={() => void setEnabled(false)}
        >
          {pending ? "Saving…" : "Disable"}
        </button>
      ) : (
        <button
          type="button"
          className="action secondary"
          disabled={pending}
          onClick={() => setConfirming("enable")}
        >
          Enable
        </button>
      )}
      <button
        type="button"
        className="action secondary"
        disabled={pending}
        onClick={() => setConfirming("delete")}
      >
        Delete
      </button>

      {confirming === "enable" && (
        <div className="pw-action-confirm" role="group" aria-label="Enable watch confirmation">
          <p>
            Enable this watch? The family switch is{" "}
            <strong>{catalog?.collection_enabled ? "on" : "off"}</strong>.{" "}
            {catalog ? (
              <>
                {catalog.enabled_count} of {catalog.max_enabled} enabled-watch slots and{" "}
                {catalog.budget_used} of {catalog.search_budget} searches per tick are already claimed.
              </>
            ) : (
              "The enabled-watch cap and search budget could not be read."
            )}
          </p>
          {!catalog?.collection_enabled && (
            <p>
              Enabling stores the choice, but no bucket is collected while{" "}
              <code>PW_COLLECTION_ENABLED</code> is false.
            </p>
          )}
          <div className="actions">
            <button type="button" className="action" disabled={pending} onClick={() => void setEnabled(true)}>
              Enable watch
            </button>
            <button type="button" className="action secondary" onClick={() => setConfirming(null)}>
              Keep disabled
            </button>
          </div>
        </div>
      )}

      {confirming === "delete" && (
        <div
          className="pw-action-confirm pw-delete-confirm"
          role="group"
          aria-label="Delete watch confirmation"
        >
          <p>
            Delete <strong>{watch.name}</strong>? This removes its aggregate values, baselines and
            incident history. A custom rule that pins one of its values will refuse the deletion
            and name every rule to detach first.
          </p>
          <div className="actions">
            <button type="button" className="action" disabled={pending} onClick={() => void remove()}>
              Delete this property watch
            </button>
            <button type="button" className="action secondary" onClick={() => setConfirming(null)}>
              Keep watch
            </button>
          </div>
        </div>
      )}
      {error && (
        <div className="banner crit pw-action-error" role="alert">
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
