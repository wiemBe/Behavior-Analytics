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
  const [historyBudgetRefusal, setHistoryBudgetRefusal] = useState(false);
  const [historyDays, setHistoryDays] = useState(catalog?.history.days_default ?? 7);

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
    if (refusal.kind === "reasons") {
      return refusal.reasons.map((reason, index) => (
        <span key={`${index}-${reason}`}>{index > 0 && <br />}{reason}</span>
      ));
    }
    if (refusal.kind === "conflict") return refusal.detail;
    return fallback;
  }

  async function setEnabled(enabled: boolean) {
    if (pending) return;
    setPending(true);
    setError(null);
    setHistoryBudgetRefusal(false);
    try {
      await api.updatePropertyWatch(watch.id, {
        expected_updated_at: watch.updated_at,
        enabled,
      });
      setConfirming(null);
      router.refresh();
    } catch (caught) {
      const refusal = classifyWatchError(caught);
      if (
        enabled &&
        refusal.kind === "conflict" &&
        /history/i.test(refusal.detail) &&
        /(budget|free|search)/i.test(refusal.detail)
      ) {
        setHistoryBudgetRefusal(true);
      }
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

  async function enableWithoutHistory() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.updatePropertyWatch(watch.id, {
        expected_updated_at: watch.updated_at,
        history_days: 0,
        enabled: true,
      });
      setHistoryBudgetRefusal(false);
      router.refresh();
    } catch (caught) {
      setError(renderRefusal(caught, "The watch could not be enabled without history. Reload it and try again."));
    } finally {
      setPending(false);
    }
  }

  async function requestHistory() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.requestPropertyWatchHistory(watch.id, historyDays);
      router.refresh();
    } catch (caught) {
      setError(renderRefusal(caught, "History could not be requested. Reload the watch and try again."));
    } finally {
      setPending(false);
    }
  }

  const canRequestHistory =
    catalog?.history.enabled === true &&
    watch.history.state === "NOT_REQUESTED" &&
    watch.coverage === null &&
    watch.collected_through === null &&
    watch.evaluated_through === null;
  const historyOptions = catalog
    ? [...new Set([7, 14, 28, catalog.history.days_default])]
        .filter((days) => days >= catalog.history.days_min && days <= catalog.history.days_max)
        .sort((a, b) => a - b)
    : [];

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
      {canRequestHistory && (
        <div className="pw-history-action" role="group" aria-label="Read history">
          <label>
            History window{" "}
            <select
              className="select-control"
              value={historyDays}
              disabled={pending}
              onChange={(event) => setHistoryDays(Number(event.target.value))}
            >
              {historyOptions.map((days) => <option key={days} value={days}>{days} days</option>)}
            </select>
          </label>
          <button type="button" className="action secondary" disabled={pending} onClick={() => void requestHistory()}>
            Read history
          </button>
        </div>
      )}

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
      {historyBudgetRefusal && (
        <div className="pw-action-confirm" role="group" aria-label="Enable without history">
          <p>
            Enable without history to start collecting at the next cadence slot. The watch then
            learns forward only; QRadar&apos;s past is not read.
          </p>
          <button type="button" className="action" disabled={pending} onClick={() => void enableWithoutHistory()}>
            Enable without history
          </button>
        </div>
      )}
    </div>
  );
}
