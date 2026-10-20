"use client";

// The decision block of one console (HISTORY-BACKFILL §B.10.3 item 1): the answer, who gave it,
// when and how, and the one or two actions the mode allows. Each action says in plain words what
// it does before it is confirmed (§B.5), and the response's own message is shown after it.

import { useId, useState } from "react";

import { ApiError, api, type HistoryConsole } from "@/lib/api";

import {
  ADMIN_PERMISSION,
  CONFIRM_TEXT,
  conflictConsole,
  decisionSentence,
  modeText,
  requestSentences,
} from "./historyFormat";

import "./history.css";

type Action = "yes" | "no" | "start" | "stop";

interface Outcome {
  tone: "ok" | "crit";
  text: string;
}

const LABEL: Record<Action, string> = {
  yes: "Read history now",
  no: "Record No",
  start: "Read history now",
  stop: "Stop reading",
};

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

function confirmText(action: Action, console: HistoryConsole, days: number | null): string {
  if (action === "yes") return CONFIRM_TEXT.yes(days);
  return CONFIRM_TEXT[action];
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
  const [confirming, setConfirming] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const actions = allowedActions(consoleState);
  const requests = requestSentences(consoleState);

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
        text: next.message ?? `Recorded. ${decisionSentence(next)}`,
      });
    } catch (err) {
      setConfirming(null);
      const recorded = conflictConsole(err);
      if (recorded !== null) {
        onChange(recorded);
        const why = err instanceof ApiError && typeof err.detail === "string" ? ` ${err.detail}.` : "";
        setOutcome({
          tone: "crit",
          text: `Not changed.${why} What is recorded now: ${decisionSentence(recorded)}`,
        });
      } else if (err instanceof ApiError && err.status === 409) {
        setOutcome({
          tone: "crit",
          text: `Not changed. ${typeof err.detail === "string" ? err.detail : "The request was refused"}.`,
        });
      } else if (err instanceof ApiError && err.status === 403) {
        setOutcome({
          tone: "crit",
          text: `Not changed. Your role cannot change the history backfill (permission ${ADMIN_PERMISSION}).`,
        });
      } else {
        setOutcome({
          tone: "crit",
          text: "Not changed. The request could not be completed. Check that the backend is reachable, then try again.",
        });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="hb-decision" aria-busy={busy}>
      <p className="hb-decision-line">{decisionSentence(consoleState)}</p>
      <p className="hb-decision-sub">
        Mode: {modeText(consoleState.mode)}.
        {requests.length > 0 ? ` ${requests.join(" ")}` : ""}
      </p>

      {!canAdmin ? (
        <p className="hb-decision-sub">
          Changing the answer needs the permission <code className="perm">{ADMIN_PERMISSION}</code>.
        </p>
      ) : confirming === null ? (
        actions.length > 0 && (
          <div className="hb-actions" role="group" aria-label={`Actions for ${consoleState.name}`}>
            {actions.map((action) => (
              <button
                key={action}
                type="button"
                className="action secondary"
                disabled={busy}
                onClick={() => setConfirming(action)}
              >
                {LABEL[action]}
              </button>
            ))}
          </div>
        )
      ) : (
        <div className="hb-confirm" role="group" aria-labelledby={`${id}-confirm`}>
          <p id={`${id}-confirm`} className="hb-confirm-title">
            {LABEL[confirming]}?
          </p>
          <p>{confirmText(confirming, consoleState, days)}</p>
          <div className="hb-actions">
            <button
              type="button"
              className="action"
              disabled={busy}
              aria-busy={busy}
              onClick={() => void run(confirming)}
            >
              {busy ? "Saving…" : `Confirm: ${LABEL[confirming]}`}
            </button>
            <button
              type="button"
              className="action secondary"
              disabled={busy}
              onClick={() => setConfirming(null)}
            >
              Cancel
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
