"use client";

// The decision block of one console (HISTORY-BACKFILL §B.10.3 item 1): the answer, who gave it,
// when and how, and the one or two actions the mode allows. Each action says in plain words what
// it does before it is confirmed (§B.5), and the response's own message is shown after it.

import { useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";

import { ApiError, api, type HistoryConsole } from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { DecisionLine, RequestLines } from "./DecisionLine";
import {
  ADMIN_PERMISSION,
  confirmText,
  conflictConsole,
  conflictDetail,
  modeText,
} from "./historyFormat";
import { sentenceReader } from "./historyReasons";

import "./history.css";

type Action = "yes" | "no" | "start" | "stop";

interface Outcome {
  tone: "ok" | "crit";
  text: ReactNode;
}

/** Which actions the console's mode allows, in the order they are offered. */
export function allowedActions(console: HistoryConsole): Action[] {
  switch (console.mode) {
    case "UNDECIDED":
      return ["yes", "no"];
    case "NO":
      return ["yes"];
    case "ACTIVE": {
      const reading = console.families.some((family) =>
        family.scopes.some((scope) => scope.state === "READING" || scope.state === "WAITING"),
      );
      return reading ? ["start", "stop"] : ["start"];
    }
    case "STOPPED":
      return ["start"];
    default:
      return [];
  }
}

export function DecisionBlock({
  console: consoleState,
  canAdmin,
  days,
  onChange,
}: {
  console: HistoryConsole;
  canAdmin: boolean;
  /** The days the decision setting asks for: the confirmation names it. */
  days: number | null;
  onChange: (next: HistoryConsole) => void;
}) {
  const id = useId();
  const t = useTranslations("history.decision");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const read = sentenceReader(i18n);
  const label = (action: Action) => t(action);
  const [confirming, setConfirming] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const actions = allowedActions(consoleState);

  async function run(action: Action) {
    setBusy(true);
    setOutcome(null);
    try {
      const next =
        action === "yes"
          ? await api.decideHistoryBackfill(consoleState.instance_id, "YES")
          : action === "no"
            ? await api.decideHistoryBackfill(consoleState.instance_id, "NO")
            : action === "start"
              ? await api.startHistoryBackfill(consoleState.instance_id)
              : await api.stopHistoryBackfill(consoleState.instance_id);
      onChange(next);
      setConfirming(null);
      setOutcome({
        tone: "ok",
        text: (next.message !== null
          ? read(next.message, { code: next.message_code, params: next.message_params })
          : null) ?? (
          <>
            {t("recorded")}
            <DecisionLine console={next} />
          </>
        ),
      });
    } catch (err) {
      setConfirming(null);
      const recorded = conflictConsole(err);
      if (recorded !== null) {
        onChange(recorded);
        const why =
          err instanceof ApiError && typeof err.detail === "string"
            ? ` ${conflictDetail(err, err.detail, read)}.`
            : "";
        setOutcome({
          tone: "crit",
          text: (
            <>
              {t("notChangedLead")}
              {why}
              {t("recordedNow")}
              <DecisionLine console={recorded} />
            </>
          ),
        });
      } else if (err instanceof ApiError && err.status === 409) {
        setOutcome({
          tone: "crit",
          text: t("refused", {
            detail:
              typeof err.detail === "string"
                ? conflictDetail(err, err.detail, read)
                : t("refusedFallback"),
          }),
        });
      } else if (err instanceof ApiError && err.status === 403) {
        setOutcome({
          tone: "crit",
          text: t("forbidden", { permission: ADMIN_PERMISSION }),
        });
      } else {
        setOutcome({
          tone: "crit",
          text: t("failed"),
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hb-decision" aria-busy={busy}>
      <p className="hb-decision-line">
        <DecisionLine console={consoleState} />
      </p>
      <p className="hb-decision-sub">
        {t("mode", { mode: modeText(consoleState.mode, i18n) })}
        <RequestLines console={consoleState} />
      </p>

      {!canAdmin ? (
        <p className="hb-decision-sub">
          {t.rich("needsPermission", {
            perm: (chunks) => <code className="perm">{chunks}</code>,
          })}
        </p>
      ) : confirming === null ? (
        actions.length > 0 && (
          <div className="hb-actions" role="group" aria-label={t("actionsFor", { name: consoleState.name })}>
            {actions.map((action) => (
              <button
                key={action}
                type="button"
                className="action secondary"
                disabled={busy}
                onClick={() => setConfirming(action)}
              >
                {label(action)}
              </button>
            ))}
          </div>
        )
      ) : (
        <div className="hb-confirm" role="group" aria-labelledby={`${id}-confirm`}>
          <p id={`${id}-confirm`} className="hb-confirm-title">
            {t("confirmTitle", { action: label(confirming) })}
          </p>
          <p>{confirmText(confirming, days, i18n)}</p>
          <div className="hb-actions">
            <button
              type="button"
              className="action"
              disabled={busy}
              aria-busy={busy}
              onClick={() => void run(confirming)}
            >
              {busy ? common("saving") : t("confirm", { action: label(confirming) })}
            </button>
            <button
              type="button"
              className="action secondary"
              disabled={busy}
              onClick={() => setConfirming(null)}
            >
              {t("cancel")}
            </button>
          </div>
        </div>
      )}

      {outcome && (
        <p className={`hb-message ${outcome.tone === "crit" ? "crit" : ""}`} role={outcome.tone === "crit" ? "alert" : "status"}>
          {outcome.text}
        </p>
      )}
    </div>
  );
}
