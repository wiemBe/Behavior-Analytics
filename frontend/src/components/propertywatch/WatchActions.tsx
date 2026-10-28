"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { api, type PropertyWatch, type PropertyWatchCatalog } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { classifyWatchError, onlyHistoryBudget } from "./authoring";

import "./propertywatch.css";

export function WatchActions({
  watch,
  catalog,
}: {
  watch: PropertyWatch;
  catalog: PropertyWatchCatalog | null;
}) {
  const t = useTranslations("propertyWatch.actions");
  const common = useTranslations("propertyWatch.common");
  const i18n = useBehaviorI18n();
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState<"enable" | "delete" | null>(null);
  const [error, setError] = useState<React.ReactNode | null>(null);
  const [historyBudgetRefusal, setHistoryBudgetRefusal] = useState(false);
  const [historyDays, setHistoryDays] = useState(catalog?.history.days_default ?? 7);

  function renderRefusal(caught: unknown, fallback: string): React.ReactNode {
    const refusal = classifyWatchError(caught, i18n);
    if (refusal.kind === "dependencies") {
      return (
        <>
          {t("dependencies", { message: refusal.message })}
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
      return t("version", {
        operator: refusal.updatedBy ?? t("anotherOperator"),
        version: refusal.currentVersion,
      });
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
      const refusal = classifyWatchError(caught, i18n);
      // The offer is made on the code, never on the wording: only the history budget alone
      // can be dropped (T-145). Another reason refuses the same attempt.
      if (enabled && refusal.kind === "conflict" && onlyHistoryBudget(refusal.codes)) {
        setHistoryBudgetRefusal(true);
      }
      setConfirming(null);
      setError(
        renderRefusal(
          caught,
          enabled ? t("enableFailed") : t("disableFailed"),
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
      setError(renderRefusal(caught, t("withoutHistoryFailed")));
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
      setError(renderRefusal(caught, t("historyFailed")));
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
      setError(renderRefusal(caught, t("deleteFailed")));
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
          {pending ? t("saving") : t("disable")}
        </button>
      ) : (
        <button
          type="button"
          className="action secondary"
          disabled={pending}
          onClick={() => setConfirming("enable")}
        >
          {t("enable")}
        </button>
      )}
      <button
        type="button"
        className="action secondary"
        disabled={pending}
        onClick={() => setConfirming("delete")}
      >
        {t("delete")}
      </button>
      {canRequestHistory && (
        <div className="pw-history-action" role="group" aria-label={t("historyGroup")}>
          <label>
            {t("historyWindow")}
            <select
              className="select-control"
              value={historyDays}
              disabled={pending}
              onChange={(event) => setHistoryDays(Number(event.target.value))}
            >
              {historyOptions.map((days) => (
                <option key={days} value={days}>{common("days", { count: days })}</option>
              ))}
            </select>
          </label>
          <button type="button" className="action secondary" disabled={pending} onClick={() => void requestHistory()}>
            {t("readHistory")}
          </button>
        </div>
      )}

      {confirming === "enable" && (
        <div className="pw-action-confirm" role="group" aria-label={t("enableConfirmLabel")}>
          <p>
            {t("enableQuestion", {
              switch: catalog?.collection_enabled ? t("switchOn") : t("switchOff"),
            })}{" "}
            {catalog ? (
              t("capacity", {
                enabled: catalog.enabled_count,
                maxEnabled: catalog.max_enabled,
                used: catalog.budget_used,
                budget: catalog.search_budget,
              })
            ) : (
              t("capacityUnknown")
            )}
          </p>
          {!catalog?.collection_enabled && (
            <p>{t.rich("enableStored", { setting: (chunks) => <code>{chunks}</code> })}</p>
          )}
          <div className="actions">
            <button type="button" className="action" disabled={pending} onClick={() => void setEnabled(true)}>
              {t("enableWatch")}
            </button>
            <button type="button" className="action secondary" onClick={() => setConfirming(null)}>
              {t("keepDisabled")}
            </button>
          </div>
        </div>
      )}

      {confirming === "delete" && (
        <div
          className="pw-action-confirm pw-delete-confirm"
          role="group"
          aria-label={t("deleteConfirmLabel")}
        >
          <p>{t.rich("deleteQuestion", {
            strong: (chunks) => <strong>{chunks}</strong>,
            name: watch.name,
          })}</p>
          <div className="actions">
            <button type="button" className="action" disabled={pending} onClick={() => void remove()}>
              {t("deleteWatch")}
            </button>
            <button type="button" className="action secondary" onClick={() => setConfirming(null)}>
              {t("keepWatch")}
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
        <div className="pw-action-confirm" role="group" aria-label={t("withoutHistoryLabel")}>
          <p>
            {t("withoutHistoryBody")}
          </p>
          <button type="button" className="action" disabled={pending} onClick={() => void enableWithoutHistory()}>
            {t("withoutHistory")}
          </button>
        </div>
      )}
    </div>
  );
}
