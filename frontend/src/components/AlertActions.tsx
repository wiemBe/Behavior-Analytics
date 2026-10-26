"use client";

// The only interactive island on the alert detail page. The page itself stays a
// server component and passes primitives across the boundary — no alert object,
// no session, no credentials.

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { actionErrorMessage, api } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";

// Mirrors AlertActionIn.reason (max_length=1000) in app/schemas/alert.py.
// The backend leaves the reason optional, so this does not force one; it only
// stops a request that the schema would reject from being sent at all.
const REASON_MAX = 1000;

type Action = "acknowledge" | "resolve";

export function AlertActions({ alertId, status }: { alertId: string; status: string }) {
  const router = useRouter();
  const t = useTranslations("alerts.actions");
  const say = asTextTranslator(useTranslations());
  const [pending, setPending] = useState<Action | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  // Resolving is terminal, so the button only asks; "Resolve alert" does it.
  const [confirming, setConfirming] = useState(false);
  const resolveRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const restoreFocus = useRef(false);

  // Focus follows the confirmation in, and goes back to Resolve when it closes.
  useEffect(() => {
    if (confirming) {
      confirmRef.current?.focus();
    } else if (restoreFocus.current) {
      restoreFocus.current = false;
      resolveRef.current?.focus();
    }
  }, [confirming]);

  function closeConfirm() {
    restoreFocus.current = true;
    setConfirming(false);
  }

  const busy = pending !== null;

  async function run(action: Action, call: () => Promise<unknown>, success: string) {
    // Belt and braces with the disabled attribute: a double-submit would enqueue
    // a second notification and write a second audit row.
    if (busy) return;
    setPending(action);
    setError(null);
    setDone(null);
    try {
      await call();
      setDone(success);
      // Re-fetch the server component so lifecycle and delivery history reflect
      // the change instead of showing stale data next to a success message.
      router.refresh();
    } catch (err) {
      setError(actionErrorMessage(err, say));
    } finally {
      setPending(null);
    }
  }

  if (status === "RESOLVED") {
    return (
      <div className="card alert-actions">
        <div className="k">{t("heading")}</div>
        <p className="subtitle alert-actions-closed">
          {t("resolved")}
        </p>
      </div>
    );
  }

  return (
    <div className="card alert-actions">
      <div className="k">{t("heading")}</div>

      <div className="actions alert-actions-row">
        {status === "OPEN" && (
          <button
            type="button"
            className="action"
            disabled={busy}
            aria-busy={pending === "acknowledge"}
            onClick={() =>
              run("acknowledge", () => api.acknowledgeAlert(alertId), t("acknowledged"))
            }
          >
            {pending === "acknowledge" ? t("acknowledging") : t("acknowledge")}
          </button>
        )}

        <input
          type="text"
          className="field"
          placeholder={t("reasonPlaceholder")}
          maxLength={REASON_MAX}
          value={reason}
          disabled={busy}
          aria-label={t("reasonLabel")}
          onChange={(e) => setReason(e.target.value)}
        />

        <button
          type="button"
          ref={resolveRef}
          className="action secondary"
          disabled={busy}
          aria-expanded={confirming}
          onClick={() => setConfirming(true)}
        >
          {t("resolve")}
        </button>
      </div>

      {confirming && (
        <div
          className="alert-actions-confirm"
          role="group"
          aria-label={t("confirmLabel")}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              closeConfirm();
            }
          }}
        >
          <p>{t("confirm")}</p>
          <div className="actions">
            <button
              type="button"
              ref={confirmRef}
              className="action"
              disabled={busy}
              aria-busy={pending === "resolve"}
              onClick={async () => {
                await run(
                  "resolve",
                  () => api.resolveAlert(alertId, reason.trim()),
                  t("resolvedDone"),
                );
                closeConfirm();
              }}
            >
              {pending === "resolve" ? t("resolving") : t("resolveAlert")}
            </button>
            <button
              type="button"
              className="action secondary"
              disabled={busy}
              onClick={closeConfirm}
            >
              {t("cancel")}
            </button>
          </div>
        </div>
      )}

      {done && (
        <p role="status" className="pill ok alert-actions-result">
          {done}
        </p>
      )}
      {error && (
        <p role="alert" className="pill crit alert-actions-result">
          {error}
        </p>
      )}
    </div>
  );
}
